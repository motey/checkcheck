from typing import Optional
import datetime
from pydantic import Field, field_validator, ValidationInfo
from sqlalchemy import text, event as _sa_event
from sqlalchemy.orm import Mapper as _SAMapper
import uuid


from sqlmodel import SQLModel, Field as SQLField


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()
import uuid


class BaseTable(SQLModel):
    # Absolute base class. All model classes will inherhit from this class.
    """
    # cast all ids to UUIDs
    @field_validator("id", check_fields=False)
    @classmethod
    def id_to_uuid(cls, v: str | uuid.UUID, info: ValidationInfo) -> uuid.UUID:
        if isinstance(v, str):
            v = uuid.UUID(v)
        return v
    """


""" 
# we can not outsource the primary key to a parent base model. sqlalchemy does not like that and throws an error in model init. e.g. 
# "sqlalchemy.exc.ArgumentError: Mapper Mapper[User(user)] could not assemble any primary key columns for mapped table 'user'"
# saaad. very saaaad.
class UUIDModel(SQLModel):
    pk: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        ## sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
"""


def naive_utc_now() -> datetime.datetime:
    """The one source of naive-UTC 'now' for row timestamps.

    Naive (tzinfo stripped) to match the project's datetime convention across
    both DB backends. Used as the ``default_factory`` for ``created_at`` /
    ``updated_at`` and by the ``before_update`` stamp so the server — never the
    client — sets the sync version signal.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class TimestampedModel(SQLModel):
    created_at: datetime.datetime = Field(
        default_factory=naive_utc_now,
        nullable=False,
    )

    # `updated_at` is the per-row version signal the 2.0 sync engine (WI-4 delta
    # feed) reads. It MUST be server-set — client clocks are never trusted for
    # ordering (LWW is server-arrival order).
    #
    # It is stamped by the `before_update` mapper event below (fires on every ORM
    # UPDATE flush, for every subclass, so no custom CRUD method can bypass it)
    # plus an explicit stamp in `CRUDBase.update` for the no-other-field-changed
    # case. We deliberately do NOT use a SQLAlchemy column `onupdate` here: on
    # multiply-inheriting `table=True` models SQLModel routes `sa_column_kwargs`
    # through `json_schema_extra`, and a callable there is not JSON-serializable,
    # which breaks OpenAPI generation. `default_factory` seeds it to ~`created_at`
    # on insert so there is no null to backfill.
    updated_at: datetime.datetime = Field(
        default_factory=naive_utc_now,
        nullable=False,
    )

    # `server_seq` is the WI-4 delta-feed cursor key: a global, strictly monotonic
    # integer stamped by the server on every insert AND update of a syncable row
    # (see the `before_insert` / `before_update` mapper events below). A client's
    # sync cursor is simply the highest `server_seq` it has applied; the delta feed
    # returns every row with `server_seq > since`.
    #
    # Chosen over an `(updated_at, id)` timestamp cursor because timestamps have
    # same-instant collisions and are not monotonic across clock adjustments. The
    # allocator (``_allocate_server_seq``) increments a single-row counter table
    # (``sync_seq``) and holds that row's lock until the surrounding transaction
    # commits, so the order in which rows *commit* matches the order of their
    # ``server_seq`` values — a reader that has consumed up to N can never miss a
    # row that commits later with a smaller seq. Nullable only for schema
    # tolerance of pre-2.0 rows; every row inserted through the ORM gets a value.
    # Uses sqlmodel's Field (not pydantic's, which the module aliases as `Field`)
    # so `index=True` actually creates the DB index the `server_seq > since`
    # delta queries scan — a plain int, so no callable leaks into json_schema_extra
    # (the OpenAPI-breaking trap the `updated_at` comment warns about).
    server_seq: Optional[int] = SQLField(default=None, nullable=True, index=True)


class GrantSeqMixin(SQLModel):
    """A per-user access-grant row whose *creation* is the canonical
    "access gained" signal for the delta feed (WI-4 / Phase 1+2 review finding 1).

    Applied to ``checklist_position`` — a row exists for exactly the users who can
    see the card, and every grant path (create, instant share, invite accept,
    public-link join, ownership transfer) inserts it at grant time. ``granted_seq``
    is stamped once on insert (to the same value as ``server_seq``) and, unlike
    ``server_seq``, is **never** bumped on update. That lets the feed tell a fresh
    grant — ship the whole card tree, whose rows predate the cursor — apart from a
    mere reorder/pin/touch of an existing position, which ``server_seq`` alone
    cannot distinguish from a first insert. Nullable only for schema tolerance of
    pre-2.0 rows; every row inserted through the ORM gets a value.
    """

    granted_seq: Optional[int] = SQLField(default=None, nullable=True, index=True)


class SoftDeleteMixin(SQLModel):
    """Tombstone marker for syncable *parent* rows (WI-2).

    ``deleted_at`` is NULL for a live row and a naive-UTC timestamp once the row
    is soft-deleted. Deletes set this instead of issuing a SQL ``DELETE`` so the
    removal propagates to offline clients through the delta feed (WI-4) and a
    stale offline edit cannot resurrect a row the server considers gone.

    Applied only to the *content parent* tables — ``checklist``, ``checklist_item``
    and ``label``. Child rows (item state/position, checklist position, the
    per-user label/collaborator link rows) are **not** tombstoned: they are masked
    by their parent's tombstone (the cascade rule). Collaborator/label link
    removal stays a hard delete in 2.0 — access-loss and label-set changes are
    re-derived by the delta feed in WI-4 (documented in VERSION_2.0_WORK_ITEMS.md).

    Garbage collection of old tombstones is explicitly deferred (2.1+).
    """

    deleted_at: Optional[datetime.datetime] = Field(default=None, nullable=True)


class SyncSequence(SQLModel, table=True):
    """Single-row global allocator behind ``TimestampedModel.server_seq`` (WI-4).

    Exactly one row (``id == SYNC_SEQ_ROW_ID``) holding the highest ``server_seq``
    handed out so far. Seeded to ``0`` right after ``create_all`` (see
    ``db/_init_db.py``); the next allocation returns ``1``. Deliberately NOT a
    ``TimestampedModel`` — it must not recurse into the stamping events below.
    A single global counter (rather than a per-table sequence) gives the delta
    feed one totally-ordered cursor across every entity.
    """

    __tablename__ = "sync_seq"
    # Optional[int] with a default is how SQLModel wants an int primary key
    # declared (a bare required int PK fails mapper PK assembly). The single row is
    # inserted with an explicit id via raw SQL in _init_db, never through the ORM.
    id: Optional[int] = SQLField(default=None, primary_key=True)
    value: int = SQLField(default=0, nullable=False)


# The one seeded row id for the global counter.
SYNC_SEQ_ROW_ID = 1


def _allocate_server_seq(connection) -> int:  # noqa: ANN001
    """Return the next global ``server_seq`` on ``connection``'s transaction.

    Runs inside a mapper flush event, so ``connection`` is the sync-facade
    connection the flush is using. The ``UPDATE`` takes a row lock on the single
    ``sync_seq`` row that is held until this transaction commits/rolls back, which
    is what makes committed ``server_seq`` values monotonic in *commit* order (see
    ``TimestampedModel.server_seq``). Two statements instead of ``UPDATE ...
    RETURNING`` so we don't depend on a minimum SQLite version.

    Deadlock note (Postgres): serialising every write through this one counter row
    means a multi-row transaction holds the counter lock alongside its other row
    locks, which widens the deadlock surface — two transactions that grab the
    counter and a data row in opposite orders can deadlock. Postgres resolves that
    by aborting one with a deadlock error (→ 5xx), which the client outbox
    classifies as *retryable* and replays; the write is idempotent, so it
    self-heals. Under real concurrency this shows up as occasional retry noise, not
    lost or skipped rows — the commit-order guarantee still holds (verified by
    ``tests/tests_server_seq_concurrency.py``). If the noise ever matters, allocate
    the seq in its own short autonomous transaction instead of the flush's.
    """
    connection.execute(
        text("UPDATE sync_seq SET value = value + 1 WHERE id = :row_id"),
        {"row_id": SYNC_SEQ_ROW_ID},
    )
    result = connection.execute(
        text("SELECT value FROM sync_seq WHERE id = :row_id"),
        {"row_id": SYNC_SEQ_ROW_ID},
    )
    return result.scalar_one()


# Server-stamp `updated_at` + allocate a fresh `server_seq` on every INSERT and
# UPDATE flush. Registered on the generic Mapper (TimestampedModel itself is
# abstract / non-mapped) and gated by an isinstance check so it applies to exactly
# the timestamped (== syncable) tables — no custom CRUD method or bulk ORM write
# can bypass it. Executing SQL on the flush's own connection here is a documented,
# supported pattern; the counter row is not itself a TimestampedModel, so there is
# no recursion.
@_sa_event.listens_for(_SAMapper, "before_insert")
def _stamp_server_seq_on_insert(mapper, connection, target):  # noqa: ANN001
    if isinstance(target, TimestampedModel):
        target.server_seq = _allocate_server_seq(connection)
        # A grant row (checklist_position) records its creation seq once, so the
        # delta feed can recognise a freshly-granted card and ship its full tree
        # (see GrantSeqMixin). Never touched by before_update, so a later reorder
        # cannot masquerade as a new grant.
        if isinstance(target, GrantSeqMixin) and target.granted_seq is None:
            target.granted_seq = target.server_seq


@_sa_event.listens_for(_SAMapper, "before_update")
def _stamp_updated_at(mapper, connection, target):  # noqa: ANN001
    if isinstance(target, TimestampedModel):
        target.updated_at = naive_utc_now()
        # A tombstone (soft delete sets deleted_at) also flows through here, so a
        # deleted row gets a fresh seq and surfaces in the delta feed.
        target.server_seq = _allocate_server_seq(connection)
