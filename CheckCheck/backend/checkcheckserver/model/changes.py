"""Response shape for the WI-4 delta feed (``GET /api/changes``).

Reuses the existing per-entity read models so nested position/state/label
serialisation matches every other endpoint (and the client's stores) exactly.
Changed rows are grouped flat per entity; removals arrive as id lists.
"""

from typing import List
import uuid

from checkcheckserver.model._base_model import BaseTable
from checkcheckserver.model.checklist import CheckListApiWithSubObj
from checkcheckserver.model.checklist_item import CheckListItemRead
from checkcheckserver.model.label import LabelReadAPI


class ChangesResponse(BaseTable):
    # The client persists this and passes it back as ``since`` next time. It is the
    # server's high-water mark read at the start of this pull, so it is safe to
    # advance to even if a row committed mid-pull (that row is simply re-sent next
    # time — last-writer-wins application is idempotent).
    next_cursor: int
    # True when the client's cursor was ahead of the server (DB reset/restore) or
    # otherwise unusable: the client must drop its cache and treat this response as
    # a full bootstrap (it was computed as if ``since=0``).
    full_resync: bool

    # Changed rows, flat per entity, in the same shapes the REST endpoints return.
    checklists: List[CheckListApiWithSubObj]
    items: List[CheckListItemRead]
    labels: List[LabelReadAPI]

    # Removals. Tombstones are rows the caller could see that were soft-deleted;
    # ``removed_checklist_ids`` are cards that dropped out of the caller's access
    # set (a revoked share) — computed by diffing the caller-supplied ``known``
    # ids against current access, since collaborator revoke is a hard delete.
    checklist_tombstones: List[uuid.UUID]
    item_tombstones: List[uuid.UUID]
    label_tombstones: List[uuid.UUID]
    removed_checklist_ids: List[uuid.UUID]
