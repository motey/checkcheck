"""Copy a card into a new card owned by the caller.

Shared by the two card actions that create a card from an existing one:

* **Duplicate** (``POST /checklist/{id}/duplicate``): every item, all unchecked.
* **Move checked items** (``POST /checklist/{id}/items/move-checked``): only the
  checked items, which keep their state and are tombstoned in the source card.

Both copy the card fields (name with a suffix, note, color, view settings) and the
caller's own labels on the source card. Shares, public links and reminders are
never copied: the new card belongs to the caller alone.

Everything is staged as ORM objects and committed in ONE transaction, so the
``before_insert`` / ``before_update`` mapper events stamp a ``server_seq`` on every
row and the delta feed ships the result (a Core bulk statement would bypass those
events; see the bulk item operations notes). "Move" tombstones the originals
instead of re-parenting them: the delta feed reports an item leaving a card only as
an item tombstone, so a re-parented row would linger on every other device of the
source card.
"""

import decimal
import uuid
from typing import Dict, Optional, Sequence

from fastapi import HTTPException, status
from sqlmodel import select, col

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.api.access import (
    attach_my_permission,
    scope_position_to_caller,
    ChecklistAccessLevel,
)
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.checklist_label import ChecklistLabelCRUD
from checkcheckserver.db.checklist_item import CheckListItemCRUD
from checkcheckserver.model.checklist import CheckList, CheckListCopyRequest
from checkcheckserver.model.checklist_position import CheckListPosition
from checkcheckserver.model.checklist_label import CheckListLabel
from checkcheckserver.model.checklist_item import CheckListItem
from checkcheckserver.model.checklist_item_state import CheckListItemState
from checkcheckserver.model.checklist_item_position import CheckListItemPosition
from checkcheckserver.model._base_model import naive_utc_now

config = Config()
log = get_logger()

DUPLICATE_NAME_SUFFIX = "(copy)"
MOVE_CHECKED_NAME_SUFFIX = "(done)"


def suffixed_name(name: Optional[str], suffix: str) -> str:
    """``"Packing list"`` -> ``"Packing list (copy)"``; an empty name -> ``"(copy)"``.
    The frontend mirrors this for its optimistic card (utils/cardCopy.ts)."""
    base = (name or "").strip()
    return f"{base} {suffix}" if base else suffix


async def find_replayed_copy(
    request: CheckListCopyRequest,
    user_id: uuid.UUID,
    checklist_crud: CheckListCRUD,
) -> Optional[CheckList]:
    """Idempotent replay check for a client-supplied card id, the same rules as the
    plain card create: the caller's live card -> it is a replay, return it; someone
    else's card -> 409 (a UUID collision); a tombstoned card -> 410 (never
    resurrect). ``None`` means the id is free (or none was given)."""
    if request.id is None:
        return None
    existing = await checklist_crud.get(id_=request.id, include_deleted=True)
    if existing is None:
        return None
    if existing.owner_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Checklist id '{request.id}' already exists.",
        )
    if existing.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_410_GONE,
            detail=f"Checklist '{request.id}' has been deleted.",
        )
    return existing


async def _resolve_item_ids(
    request: CheckListCopyRequest,
    source_items: Sequence[CheckListItem],
    checklist_item_crud: CheckListItemCRUD,
) -> Dict[uuid.UUID, uuid.UUID]:
    """The id each copied item gets: the client's choice where it supplied one, a
    fresh uuid4 otherwise. Rejects a client map that reuses an id (422) or names an
    id an item already has (409)."""
    source_ids = {item.id for item in source_items}
    wanted = {src: dst for src, dst in request.item_ids.items() if src in source_ids}
    if len(set(wanted.values())) != len(wanted):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="item_ids maps two items to the same id.",
        )
    if wanted:
        taken = await checklist_item_crud.session.exec(
            select(CheckListItem.id).where(col(CheckListItem.id).in_(wanted.values()))
        )
        taken_ids = list(taken.all())
        if taken_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Item id '{taken_ids[0]}' already exists.",
            )
    return {item.id: wanted.get(item.id) or uuid.uuid4() for item in source_items}


async def _next_board_index(
    user_id: uuid.UUID, checklist_position_crud: CheckListPositionCRUD
) -> float:
    """Top of the caller's board: one 0.4 step past the highest index (the same
    placement as a plain card create)."""
    highest = await checklist_position_crud.get_last(user_id=user_id)
    if highest is None:
        return 0
    return float(decimal.Decimal(str(highest.index)) + decimal.Decimal("0.4"))


async def copy_checklist(
    *,
    source: CheckList,
    request: CheckListCopyRequest,
    user_id: uuid.UUID,
    name_suffix: str,
    source_items: Sequence[CheckListItem],
    keep_checked_state: bool,
    tombstone_source_items: bool,
    checklist_crud: CheckListCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    checklist_label_crud: ChecklistLabelCRUD,
    checklist_item_crud: CheckListItemCRUD,
) -> CheckList:
    """Create the new card from ``source`` with copies of ``source_items`` (in the
    given order) and commit once. Returns the new card, response-ready (caller's
    position, caller's labels, ``my_permission = owner``).

    ``keep_checked_state=False`` unchecks every copy (duplicate); ``True`` keeps
    each item's state (move). ``tombstone_source_items=True`` soft-deletes the
    originals in the same transaction (move)."""
    session = checklist_crud.session
    item_ids = await _resolve_item_ids(request, source_items, checklist_item_crud)
    # The caller's own labels on the source (labels are per user; another
    # collaborator's labels are private to them).
    labels = await checklist_label_crud.list_labels_for_user(
        checklist_id=source.id, user_id=user_id
    )
    if request.position is not None and request.position.index is not None:
        board_index = request.position.index
    else:
        board_index = await _next_board_index(user_id, checklist_position_crud)

    card = CheckList(
        id=request.id or uuid.uuid4(),
        owner_id=user_id,
        name=suffixed_name(source.name, name_suffix),
        text=source.text,
        color_id=source.color_id,
        checked_items_seperated=source.checked_items_seperated,
        checked_items_collapsed=source.checked_items_collapsed,
        suggest_existing_items=source.suggest_existing_items,
    )
    position = CheckListPosition(
        checklist_id=card.id,
        user_id=user_id,
        index=board_index,
        pinned=False,
        archived=False,
    )
    session.add(card)
    session.add(position)
    # Flush the card first: the item and label rows reference it by foreign key
    # only (no ORM relationship), so the unit of work cannot order them itself.
    await session.flush()

    for source_item in source_items:
        new_item_id = item_ids[source_item.id]
        session.add(
            CheckListItem(id=new_item_id, checklist_id=card.id, text=source_item.text)
        )
        session.add(
            CheckListItemPosition(
                checklist_item_id=new_item_id,
                index=source_item.position.index,
                indentation=source_item.position.indentation,
            )
        )
        session.add(
            CheckListItemState(
                checklist_item_id=new_item_id,
                checked=source_item.state.checked if keep_checked_state else False,
            )
        )
    for label in labels:
        session.add(
            CheckListLabel(checklist_id=card.id, label_id=label.id, user_id=user_id)
        )
    if tombstone_source_items:
        # Tombstone the parent item rows only; their state/position children stay,
        # masked by the tombstone (hard-deleting a child in this session would trip
        # the cascade crash, see the tombstone notes in docs/development.md).
        now = naive_utc_now()
        for source_item in source_items:
            source_item.deleted_at = now
            session.add(source_item)
    await session.commit()
    return await load_copy_response(card.id, user_id, checklist_crud, checklist_position_crud, checklist_label_crud)


async def load_copy_response(
    checklist_id: uuid.UUID,
    user_id: uuid.UUID,
    checklist_crud: CheckListCRUD,
    checklist_position_crud: CheckListPositionCRUD,
    checklist_label_crud: ChecklistLabelCRUD,
) -> CheckList:
    """The new card as the caller sees it (also used for an idempotent replay)."""
    card = await checklist_crud.get(id_=checklist_id)
    # Reload so the joined relationships (position, color, owner) are populated:
    # the identity map hands back the object staged above, whose relationships
    # were never loaded, and a lazy load on the async session would fail.
    await checklist_crud.session.refresh(card)
    user_position = await checklist_position_crud.get(
        checklist_id=checklist_id, user_id=user_id
    )
    scope_position_to_caller(card, user_position)
    attach_my_permission(card, ChecklistAccessLevel.owner)
    # Last, so no query runs after the in-memory reassignment (never committed).
    card.labels = await checklist_label_crud.list_labels_for_user(
        checklist_id=checklist_id, user_id=user_id
    )
    return card
