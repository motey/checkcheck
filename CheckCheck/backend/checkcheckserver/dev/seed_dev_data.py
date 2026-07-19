"""Generative, parameterizable dev-data seeder.

Fills a local CheckCheck database with *diverse* checklists so a human can
eyeball the UI against realistic content: long and short lists, every colour,
every settings combination, items that are one word / a paragraph / multi-line /
Markdown / emoji, lists shared out to other users, lists received from other
users (including still-pending invites), a living group share, and public links.

The main target account is the OIDC mock user ``admin`` — everything owned by
"me" is owned by that account, and the shares/invites are wired so they show up
on admin's board and in admin's notifications.

Design notes
------------
* **Runs standalone, before the server.** It performs the same idempotent
  ``init_schema_and_migrations()`` + ``init_db()`` the server does on boot, then
  writes data, so it can run on a fresh (``--reset``) database with no race
  against a live server. Booting the server afterwards re-runs those inits
  harmlessly.
* **Deterministic.** Every id is derived from ``--seed`` via ``uuid5`` and all
  random choices come from a seeded ``random.Random``. Re-running with the same
  seed reproduces the same board. A different seed produces a fresh, disjoint set
  that coexists with the old one.
* **Idempotent.** By default a second run detects its own sentinel and skips.
  ``--wipe`` deletes exactly the rows this seed produced (by deterministic
  checklist id) and regenerates them; it never touches hand-made data.
* **Below the HTTP layer.** It writes through the ORM models directly (mirroring
  the routes' checklist+position and item+position+state assembly), so the
  ``before_insert`` mapper events still stamp ``server_seq`` — the seeded rows are
  real syncable rows, indistinguishable from ones a client would create.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import random
import sys
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from sqlmodel import col, delete, select

from checkcheckserver.config import Config, DbBackend
from checkcheckserver.log import get_logger

# Import every table model so they are registered on SQLModel.metadata before
# init_schema_and_migrations() runs create_all — otherwise fresh-DB creation
# would miss tables this seeder writes to (e.g. checklist_group_share).
import checkcheckserver.model._tables  # noqa: F401

from checkcheckserver.dev import _seed_content as content

log = get_logger()
config = Config()

# Stable namespace so ids only depend on (seed, category, index) — never on the
# machine or the run. Do not change this value or every seed's ids shift.
_SEED_NAMESPACE = uuid.UUID("f1d0c0de-0000-4000-8000-c4ecc4ec5eed")

# The ten colour scheme ids provisioned by default_data.yaml.
COLOR_IDS = [
    "blue",
    "turquoise",
    "glaucous",
    "lilac",
    "finn",
    "magenta_haze",
    "red",
    "orange",
    "coffee",
    "yellow",
]

# OIDC mock counterpart accounts (see dev_oidc_server/testusers.yaml). Used as the
# other side of every share. `groups` mirrors the mock so a real login syncs to
# the same values and the living group-share reconcile behaves identically.
SECONDARY_USERS = [
    {"user_name": "admin2", "display_name": "Admin Two", "email": "admin2@test.com",
     "roles": ["admin"], "groups": ["others", "icecream-lovers", "medlog-admins"]},
    {"user_name": "user1", "display_name": "User One", "email": "user1@test.com",
     "roles": [], "groups": ["others", "icecream-lovers"]},
    {"user_name": "user2", "display_name": "User Two", "email": "user2@test.com",
     "roles": [], "groups": ["others"]},
    {"user_name": "user3", "display_name": "User Three", "email": "user3@test.com",
     "roles": [], "groups": ["others"]},
]

# Groups the admin account belongs to per the mock — used for group shares that
# admin should receive (via the login-time reconcile) or hand out.
ADMIN_GROUPS = ["medlog-admins", "others", "icecream-lovers"]


def det_uuid(seed: int, *parts: object) -> uuid.UUID:
    """Deterministic uuid from the seed and a category path."""
    name = ":".join([str(seed)] + [str(p) for p in parts])
    return uuid.uuid5(_SEED_NAMESPACE, name)


# ── Profiles ───────────────────────────────────────────────────────────────────
@dataclass
class Profile:
    """How much of each kind of data to generate. CLI flags override any field."""

    owned_lists: int          # admin-owned lists (on top of the guaranteed set)
    shared_by_me: int         # admin-owned lists shared out to other users
    shared_with_me: int       # other-owned lists shared to admin (incl. pending)
    group_shares: int         # other-owned lists shared to a group admin is in
    public_links: int         # admin-owned lists that get a public share link
    max_items: int            # upper bound for the "long" lists

    @staticmethod
    def named(name: str) -> "Profile":
        table = {
            "small": Profile(owned_lists=4, shared_by_me=2, shared_with_me=2,
                             group_shares=1, public_links=1, max_items=25),
            "medium": Profile(owned_lists=10, shared_by_me=4, shared_with_me=4,
                              group_shares=2, public_links=2, max_items=60),
            "large": Profile(owned_lists=24, shared_by_me=8, shared_with_me=8,
                             group_shares=3, public_links=3, max_items=120),
        }
        if name not in table:
            raise SystemExit(f"Unknown profile '{name}'. Choose small|medium|large.")
        return table[name]


@dataclass
class ItemSpec:
    text: str
    checked: bool


@dataclass
class ListSpec:
    """A fully-resolved checklist to be written, plus its per-user position."""

    category: str
    idx: int
    name: str
    text: Optional[str]
    color_id: Optional[str]
    checked_items_seperated: bool
    checked_items_collapsed: bool
    suggest_existing_items: bool
    pinned: bool
    archived: bool
    items: List[ItemSpec] = field(default_factory=list)
    label_slots: List[int] = field(default_factory=list)  # indexes into admin labels

    def checklist_id(self, seed: int) -> uuid.UUID:
        return det_uuid(seed, self.category, self.idx)


# ── Content generation ──────────────────────────────────────────────────────────
def _make_items(rng: random.Random, count: int, flavor: str, checked_ratio: float) -> List[ItemSpec]:
    banks = {
        "short": content.SHORT_ITEMS,
        "long": content.LONG_ITEMS,
        "multiline": content.MULTILINE_ITEMS,
        "markdown": content.MARKDOWN_ITEMS,
        "url": content.URL_ITEMS,
        "fancy": content.FANCY_ITEMS,
    }
    items: List[ItemSpec] = []
    for _ in range(count):
        if flavor == "mixed":
            # Weighted toward short (like a real list) but regularly sprinkling the
            # awkward shapes we actually want to eyeball.
            pick = rng.choices(
                ["short", "long", "multiline", "markdown", "url", "fancy"],
                weights=[46, 11, 13, 13, 8, 9],
            )[0]
        else:
            pick = flavor
        text = rng.choice(banks[pick])
        items.append(ItemSpec(text=text, checked=rng.random() < checked_ratio))
    return items


def _owned_specs(rng: random.Random, profile: Profile, n_labels: int) -> List[ListSpec]:
    """Admin-owned lists: a guaranteed set covering every edge shape, then random
    padding up to the profile count."""
    specs: List[ListSpec] = []
    used_names: set = set()

    def name(fancy: bool = False) -> str:
        pool = content.LIST_NAMES_FANCY if fancy else content.LIST_NAMES
        choices = [n for n in pool if n not in used_names] or pool
        n = rng.choice(choices)
        used_names.add(n)
        return n

    def note(rng: random.Random) -> Optional[str]:
        return rng.choice(
            [None, None]
            + content.NOTES_SHORT
            + content.NOTES_MARKDOWN
            + content.NOTES_MULTILINE
        )

    def labels(k: int) -> List[int]:
        if n_labels == 0 or k == 0:
            return []
        return rng.sample(range(n_labels), k=min(k, n_labels))

    idx = 0

    def add(**kw) -> None:
        nonlocal idx
        defaults = dict(
            category="owned",
            idx=idx,
            name=name(kw.pop("fancy", False)),
            text=note(rng),
            color_id=rng.choice(COLOR_IDS + [None]),
            checked_items_seperated=True,
            checked_items_collapsed=False,
            suggest_existing_items=True,
            pinned=False,
            archived=False,
            items=[],
            label_slots=labels(rng.randint(0, 2)),
        )
        defaults.update(kw)
        specs.append(ListSpec(**defaults))
        idx += 1

    # 1. Guaranteed edge shapes ------------------------------------------------
    # Empty list.
    add(name="Empty list (nothing yet)", items=[], color_id=None)
    # Tiny, all unchecked.
    add(items=_make_items(rng, rng.randint(2, 3), "short", 0.0))
    # Short, all checked, collapsed + separated so the "done" section shows.
    add(items=_make_items(rng, rng.randint(6, 9), "short", 1.0),
        checked_items_collapsed=True, pinned=True,
        color_id=rng.choice(COLOR_IDS))
    # Multi-line heavy.
    add(items=_make_items(rng, rng.randint(4, 6), "multiline", 0.3),
        text=rng.choice(content.NOTES_MULTILINE))
    # Markdown heavy, with a Markdown note.
    add(items=_make_items(rng, rng.randint(4, 6), "markdown", 0.4),
        text=rng.choice(content.NOTES_MARKDOWN))
    # URL heavy — every item carries a link, so the boxed-arrow "open" affordance
    # (and long-URL truncation) is always visible on a seeded board.
    add(items=_make_items(rng, rng.randint(4, 6), "url", 0.2),
        text="Links collected here. Tap the arrow to open one.")
    # Long paragraphs.
    add(items=_make_items(rng, rng.randint(6, 10), "long", 0.25))
    # Emoji / unicode name + items.
    add(fancy=True, items=_make_items(rng, rng.randint(5, 8), "fancy", 0.5))
    # One big list.
    add(items=_make_items(rng, rng.randint(max(20, profile.max_items // 2), profile.max_items), "mixed", 0.45),
        checked_items_collapsed=True)
    # Archived list (still owned, shows in the archive view).
    add(items=_make_items(rng, rng.randint(3, 8), "mixed", 0.6), archived=True)
    # checked-items-NOT-separated + suggestions off.
    add(items=_make_items(rng, rng.randint(8, 14), "mixed", 0.5),
        checked_items_seperated=False, suggest_existing_items=False,
        color_id=rng.choice(COLOR_IDS))

    # 2. Random padding up to the requested count -------------------------------
    while len([s for s in specs]) < max(len(specs), profile.owned_lists):
        add(items=_make_items(rng, rng.randint(0, 18), "mixed", rng.uniform(0.0, 0.7)),
            checked_items_collapsed=rng.random() < 0.3,
            checked_items_seperated=rng.random() < 0.8,
            suggest_existing_items=rng.random() < 0.7,
            pinned=rng.random() < 0.2,
            color_id=rng.choice(COLOR_IDS + [None]))

    return specs


def _foreign_specs(rng: random.Random, category: str, count: int, max_items: int) -> List[ListSpec]:
    """Lists owned by *other* users (received / group-shared to admin)."""
    specs: List[ListSpec] = []
    for i in range(count):
        fancy = rng.random() < 0.25
        pool = content.LIST_NAMES_FANCY if fancy else content.LIST_NAMES
        specs.append(ListSpec(
            category=category,
            idx=i,
            name=rng.choice(pool),
            text=rng.choice([None] + content.NOTES_SHORT + content.NOTES_MARKDOWN),
            color_id=rng.choice(COLOR_IDS + [None]),
            checked_items_seperated=rng.random() < 0.8,
            checked_items_collapsed=rng.random() < 0.3,
            suggest_existing_items=rng.random() < 0.7,
            pinned=False,
            archived=False,
            items=_make_items(rng, rng.randint(3, min(20, max_items)), "mixed", rng.uniform(0.1, 0.6)),
        ))
    return specs


# ── DB writing ───────────────────────────────────────────────────────────────
class Seeder:
    def __init__(self, session, seed: int, args) -> None:
        self.session = session
        self.seed = seed
        self.args = args
        self.rng = random.Random(seed)

    # -- low level helpers --
    async def _commit(self) -> None:
        await self.session.commit()

    def _next_position_index(self, counter: List[float]) -> float:
        counter[0] += 1.0
        return counter[0]

    async def _write_checklist(self, spec: ListSpec, owner_id: uuid.UUID, owner_pos_index: float) -> uuid.UUID:
        """Write a checklist + its owner position + all items (each with its own
        position and state), committed as the routes do."""
        from checkcheckserver.model.checklist import CheckList
        from checkcheckserver.model.checklist_position import CheckListPosition
        from checkcheckserver.model.checklist_item import CheckListItem
        from checkcheckserver.model.checklist_item_position import CheckListItemPosition
        from checkcheckserver.model.checklist_item_state import CheckListItemState

        cl_id = spec.checklist_id(self.seed)
        self.session.add(CheckList(
            id=cl_id,
            name=spec.name,
            text=spec.text,
            color_id=spec.color_id,
            checked_items_seperated=spec.checked_items_seperated,
            checked_items_collapsed=spec.checked_items_collapsed,
            suggest_existing_items=spec.suggest_existing_items,
            owner_id=owner_id,
        ))
        self.session.add(CheckListPosition(
            checklist_id=cl_id,
            user_id=owner_id,
            index=owner_pos_index,
            pinned=1 if spec.pinned else 0,
            archived=spec.archived,
            checked_items_collapsed=spec.checked_items_collapsed,
        ))
        for i, item in enumerate(spec.items):
            item_id = det_uuid(self.seed, spec.category, spec.idx, "item", i)
            # Occasionally indent a non-first item to exercise sub-items.
            indentation = 1 if (i > 0 and self.rng.random() < 0.12) else 0
            self.session.add(CheckListItem(id=item_id, checklist_id=cl_id, text=item.text))
            self.session.add(CheckListItemPosition(
                checklist_item_id=item_id, index=float(i), indentation=indentation,
            ))
            self.session.add(CheckListItemState(checklist_item_id=item_id, checked=item.checked))
        await self._commit()
        return cl_id

    async def _attach_labels(self, cl_id: uuid.UUID, user_id: uuid.UUID, label_ids: Sequence[uuid.UUID]) -> None:
        from checkcheckserver.model.checklist_label import CheckListLabel

        for label_id in label_ids:
            self.session.add(CheckListLabel(checklist_id=cl_id, label_id=label_id, user_id=user_id))
        if label_ids:
            await self._commit()

    async def _add_collaborator(
        self,
        cl_id: uuid.UUID,
        user_id: uuid.UUID,
        permission,
        status,
        grid_index: Optional[float],
    ) -> None:
        """Collaborator row (+ grid position when the status grants access). A
        pending/declined invite intentionally gets NO position, matching the real
        invite gate (the grid query inner-joins the position)."""
        from checkcheckserver.model.checklist_collaborator import CheckListCollaborator, ShareStatus
        from checkcheckserver.model.checklist_position import CheckListPosition

        self.session.add(CheckListCollaborator(
            checklist_id=cl_id,
            user_id=user_id,
            permission=permission,
            status=status,
            via_group=None,
        ))
        if status == ShareStatus.accepted and grid_index is not None:
            self.session.add(CheckListPosition(
                checklist_id=cl_id, user_id=user_id, index=grid_index,
                pinned=0, archived=False,
            ))
        await self._commit()

    # -- users --
    async def resolve_admin(self) -> "object":
        from checkcheckserver.db.user import UserCRUD
        from checkcheckserver.model.user import UserCreate

        crud = UserCRUD(self.session)
        admin = await crud.get_by_user_name(user_name=self.args.admin_user, show_deactivated=True)
        if admin is None:
            log.info(f"[seed] admin '{self.args.admin_user}' not found — creating it")
            admin = await crud.create(UserCreate(
                user_name=self.args.admin_user,
                display_name="Admin Maier",
                email="admin@test.com",
                roles=[config.ADMIN_ROLE_NAME],
                oidc_groups=ADMIN_GROUPS,
            ), exists_ok=True)
        return admin

    async def ensure_secondary_users(self) -> Dict[str, "object"]:
        from checkcheckserver.db.user import UserCRUD
        from checkcheckserver.model.user import UserCreate

        crud = UserCRUD(self.session)
        out: Dict[str, object] = {}
        for spec in SECONDARY_USERS:
            existing = await crud.get_by_user_name(user_name=spec["user_name"], show_deactivated=True)
            if existing is None:
                existing = await crud.create(UserCreate(
                    user_name=spec["user_name"],
                    display_name=spec["display_name"],
                    email=spec["email"],
                    roles=spec["roles"],
                    oidc_groups=spec["groups"],
                ), exists_ok=True)
            out[spec["user_name"]] = existing
        return out

    async def create_admin_labels(self, admin_id: uuid.UUID) -> List[uuid.UUID]:
        """A handful of deterministic extra labels for admin, on top of the
        auto-created defaults. Returns their ids."""
        from checkcheckserver.model.label import Label

        n = min(6, len(content.LABEL_NAMES))
        names = self.rng.sample(content.LABEL_NAMES, k=n)
        ids: List[uuid.UUID] = []
        for i, nm in enumerate(names):
            label_id = det_uuid(self.seed, "label", i)
            self.session.add(Label(
                id=label_id,
                owner_id=admin_id,
                display_name=nm,
                color_id=self.rng.choice(COLOR_IDS + [None]),
                sort_order=100 + i,
            ))
            ids.append(label_id)
        await self._commit()
        return ids

    # -- idempotency --
    def _all_checklist_ids(self, profile: Profile) -> List[uuid.UUID]:
        """Every checklist id this (seed, profile) would produce — used by --wipe.
        Generated without RNG so it stays stable regardless of content sampling."""
        ids: List[uuid.UUID] = []
        # owned: the guaranteed set has a fixed size (10) plus padding to profile.
        owned_count = max(10, profile.owned_lists)
        for i in range(owned_count):
            ids.append(det_uuid(self.seed, "owned", i))
        for i in range(profile.shared_by_me):
            ids.append(det_uuid(self.seed, "shared_by_me", i))
        for i in range(profile.shared_with_me):
            ids.append(det_uuid(self.seed, "shared_with_me", i))
        for i in range(profile.group_shares):
            ids.append(det_uuid(self.seed, "group_share", i))
        # public links reuse owned lists — no new checklists.
        return ids

    async def already_seeded(self) -> bool:
        from checkcheckserver.model.checklist import CheckList

        sentinel = det_uuid(self.seed, "owned", 0)
        res = await self.session.exec(select(CheckList.id).where(CheckList.id == sentinel))
        return res.first() is not None

    async def wipe(self, profile: Profile) -> None:
        from checkcheckserver.model.checklist import CheckList
        from checkcheckserver.model.checklist_item import CheckListItem
        from checkcheckserver.model.checklist_item_position import CheckListItemPosition
        from checkcheckserver.model.checklist_item_state import CheckListItemState
        from checkcheckserver.model.checklist_position import CheckListPosition
        from checkcheckserver.model.checklist_label import CheckListLabel
        from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
        from checkcheckserver.model.checklist_group_share import CheckListGroupShare
        from checkcheckserver.model.checklist_public_share import CheckListPublicShare
        from checkcheckserver.model.label import Label

        cl_ids = self._all_checklist_ids(profile)
        log.info(f"[seed] --wipe: removing {len(cl_ids)} previously-seeded checklists")

        # SQLite honours FK cascades only with the pragma on; we delete child-first
        # explicitly anyway so behaviour is identical on both backends.
        if config.db_backend == DbBackend.SQLITE:
            from sqlalchemy import text
            await self.session.exec(text("PRAGMA foreign_keys = ON;"))

        # item children keyed by item id -> gather item ids first
        item_ids_res = await self.session.exec(
            select(CheckListItem.id).where(col(CheckListItem.checklist_id).in_(cl_ids))
        )
        item_ids = list(item_ids_res.all())
        if item_ids:
            await self.session.exec(delete(CheckListItemState).where(col(CheckListItemState.checklist_item_id).in_(item_ids)))
            await self.session.exec(delete(CheckListItemPosition).where(col(CheckListItemPosition.checklist_item_id).in_(item_ids)))
        for model in (CheckListItem, CheckListPosition, CheckListLabel,
                      CheckListCollaborator, CheckListGroupShare, CheckListPublicShare):
            await self.session.exec(delete(model).where(col(model.checklist_id).in_(cl_ids)))
        await self.session.exec(delete(CheckList).where(col(CheckList.id).in_(cl_ids)))

        # seeded admin labels (deterministic ids)
        label_ids = [det_uuid(self.seed, "label", i) for i in range(len(content.LABEL_NAMES))]
        await self.session.exec(delete(CheckListLabel).where(col(CheckListLabel.label_id).in_(label_ids)))
        await self.session.exec(delete(Label).where(col(Label.id).in_(label_ids)))
        await self._commit()

    # -- orchestration --
    async def run(self, profile: Profile) -> None:
        from checkcheckserver.model.checklist_collaborator import SharePermission, ShareStatus
        from checkcheckserver.model.checklist_group_share import CheckListGroupShare
        from checkcheckserver.model.checklist_public_share import CheckListPublicShare

        admin = await self.resolve_admin()
        secondary = await self.ensure_secondary_users()
        secondary_list = list(secondary.values())
        label_ids = await self.create_admin_labels(admin.id)

        admin_pos = [0.0]   # running grid index for admin
        foreign_pos: Dict[uuid.UUID, List[float]] = {}

        def fpos(uid: uuid.UUID) -> float:
            counter = foreign_pos.setdefault(uid, [0.0])
            counter[0] += 1.0
            return counter[0]

        # 1. Admin-owned lists ---------------------------------------------------
        owned = _owned_specs(self.rng, profile, len(label_ids))
        owned_ids: List[uuid.UUID] = []
        for spec in owned:
            cl_id = await self._write_checklist(spec, admin.id, self._next_position_index(admin_pos))
            await self._attach_labels(cl_id, admin.id, [label_ids[i] for i in spec.label_slots])
            owned_ids.append(cl_id)
        log.info(f"[seed] created {len(owned_ids)} admin-owned lists")

        # 2. Admin-owned lists shared OUT to other users (shared_by_me) ----------
        share_specs = _foreign_specs(self.rng, "shared_by_me", profile.shared_by_me, profile.max_items)
        n_shared_out = 0
        for spec in share_specs:
            spec_owner_pos = self._next_position_index(admin_pos)
            cl_id = await self._write_checklist(spec, admin.id, spec_owner_pos)
            # share to 1..2 random secondary users with assorted permission/status.
            targets = self.rng.sample(secondary_list, k=self.rng.randint(1, min(2, len(secondary_list))))
            for target in targets:
                permission = self.rng.choice([SharePermission.view, SharePermission.check, SharePermission.edit])
                status = self.rng.choices(
                    [ShareStatus.accepted, ShareStatus.pending, ShareStatus.declined],
                    weights=[70, 20, 10],
                )[0]
                await self._add_collaborator(cl_id, target.id, permission, status, fpos(target.id))
            n_shared_out += 1
        log.info(f"[seed] created {n_shared_out} lists shared out by admin")

        # 3. Other-owned lists shared TO admin (shared_with_me / received) -------
        recv_specs = _foreign_specs(self.rng, "shared_with_me", profile.shared_with_me, profile.max_items)
        n_recv = 0
        for i, spec in enumerate(recv_specs):
            owner = secondary_list[i % len(secondary_list)]
            cl_id = await self._write_checklist(spec, owner.id, fpos(owner.id))
            permission = self.rng.choice([SharePermission.view, SharePermission.check, SharePermission.edit])
            # Guarantee at least one pending invite so the invites UI has something.
            if i == 0:
                status = ShareStatus.pending
            else:
                status = self.rng.choices(
                    [ShareStatus.accepted, ShareStatus.pending], weights=[75, 25]
                )[0]
            await self._add_collaborator(
                cl_id, admin.id, permission, status,
                self._next_position_index(admin_pos) if status == ShareStatus.accepted else None,
            )
            n_recv += 1
        log.info(f"[seed] created {n_recv} lists shared to admin (received)")

        # 4. Group shares --------------------------------------------------------
        # (a) other-owned lists shared to a group admin is in. Admin's per-user
        #     collaborator + position are materialized by the login-time reconcile
        #     (group_share_reconcile.reconcile_user) — exercising the real path.
        group_specs = _foreign_specs(self.rng, "group_share", profile.group_shares, profile.max_items)
        for i, spec in enumerate(group_specs):
            owner = secondary_list[i % len(secondary_list)]
            cl_id = await self._write_checklist(spec, owner.id, fpos(owner.id))
            group = self.rng.choice(["icecream-lovers", "others"])
            permission = self.rng.choice([SharePermission.view, SharePermission.check, SharePermission.edit])
            self.session.add(CheckListGroupShare(
                checklist_id=cl_id, group=group, permission=permission, created_by=owner.id,
            ))
            await self._commit()
        # (b) one admin-owned list shared out to a group (shared-by-me via group).
        if owned_ids:
            self.session.add(CheckListGroupShare(
                checklist_id=owned_ids[0], group="others",
                permission=SharePermission.check, created_by=admin.id,
            ))
            await self._commit()
        log.info(f"[seed] created {len(group_specs) + (1 if owned_ids else 0)} group shares")

        # 5. Public links on some admin-owned lists ------------------------------
        n_public = min(profile.public_links, len(owned_ids))
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        for i in range(n_public):
            cl_id = owned_ids[i]
            # Vary: never-expiring enabled, future-expiry enabled, disabled.
            variant = i % 3
            enabled = variant != 2
            expires_at = now + datetime.timedelta(days=30) if variant == 1 else None
            self.session.add(CheckListPublicShare(
                id=det_uuid(self.seed, "public_share", i),
                checklist_id=cl_id,
                token=det_uuid(self.seed, "public_token", i).hex,
                permission=SharePermission.view,
                enabled=enabled,
                expires_at=expires_at,
                created_by=admin.id,
            ))
            await self._commit()
        log.info(f"[seed] created {n_public} public links")


# ── Entry point ──────────────────────────────────────────────────────────────
def _parse_args(argv: Sequence[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="seed_dev_data",
        description="Fill the local CheckCheck DB with diverse random dev data.",
    )
    p.add_argument("--seed", type=int, default=1337, help="RNG seed (deterministic ids).")
    p.add_argument("--profile", default="medium", choices=["small", "medium", "large"],
                   help="Preset data volume (default: medium).")
    p.add_argument("--admin-user", default=config.ADMIN_USER_NAME,
                   help="user_name of the account that owns 'my' data (default: the OIDC/admin user).")
    p.add_argument("--wipe", action="store_true",
                   help="Delete this seed's previously-generated data first, then regenerate.")
    # Per-field overrides on top of the profile.
    p.add_argument("--owned-lists", type=int, default=None)
    p.add_argument("--shared-by-me", type=int, default=None)
    p.add_argument("--shared-with-me", type=int, default=None)
    p.add_argument("--group-shares", type=int, default=None)
    p.add_argument("--public-links", type=int, default=None)
    p.add_argument("--max-items", type=int, default=None)
    return p.parse_args(argv)


def _resolve_profile(args: argparse.Namespace) -> Profile:
    profile = Profile.named(args.profile)
    for attr in ("owned_lists", "shared_by_me", "shared_with_me",
                 "group_shares", "public_links", "max_items"):
        override = getattr(args, attr)
        if override is not None:
            setattr(profile, attr, override)
    return profile


async def _seed_async(args: argparse.Namespace) -> None:
    from checkcheckserver.db._init_db import init_db
    from checkcheckserver.db._session import get_async_session_context

    # Same async init the server runs on boot (admin user + provisioning). Idempotent.
    await init_db()

    profile = _resolve_profile(args)
    async with get_async_session_context() as session:
        seeder = Seeder(session, args.seed, args)
        if args.wipe:
            await seeder.wipe(profile)
        elif await seeder.already_seeded():
            log.info(
                f"[seed] seed {args.seed} already present — nothing to do "
                f"(use --wipe to regenerate)."
            )
            return
        await seeder.run(profile)
    log.info("[seed] done.")


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    log.info(
        f"[seed] seeding dev data: profile={args.profile} seed={args.seed} "
        f"admin_user={args.admin_user} wipe={args.wipe}"
    )
    # Mirror main.py: synchronous schema/migrations first (its own loop), then the
    # async work in a fresh loop with clean connections.
    from checkcheckserver.db._init_db import init_schema_and_migrations

    init_schema_and_migrations()
    asyncio.run(_seed_async(args))


if __name__ == "__main__":
    main()
