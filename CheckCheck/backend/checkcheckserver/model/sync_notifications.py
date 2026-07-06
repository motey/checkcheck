from typing import List, Optional, Literal
from pydantic import BaseModel
import uuid
import time
from sqlmodel import Field, String, Column, JSON

from checkcheckserver.model._base_model import BaseTable


class SyncNotification(BaseTable, table=True):
    id: Optional[int] = Field(
        default=None,
        primary_key=True,
        description="Monotonic insertion order; used by the SQLite drain loop",
    )
    timestamp: float = Field(
        description="Creation time of the notification",
        index=True,
        default_factory=time.time,
    )
    cl_id: uuid.UUID = Field(
        description="Checklist that has to be updated by the client"
    )
    cli_id: Optional[uuid.UUID] = Field(
        default=None,
        description="ID of Checklist item that has to be updated by the client",
    )
    upd_prop: Literal[
        "item_state",
        "item_text",
        "item_position",
        "item_created",
        "item_deleted",
        "checklist",
        "checklist_position",
        "checklist_created",
        "checklist_deleted",
        "checklist_label",
        "share_added",
        "share_invited",
        "share_removed",
        "notification",
        # WI-5 "changes available" poke. Emitted alongside every board-mutating
        # per-entity event above (never on its own). Carries server_seq; a
        # local-first client uses it as its single "pull GET /api/changes" signal.
        # The legacy frontend ignores it (unknown upd_prop → no-op switch branch).
        "changes_available",
    ] = Field(default=None, sa_type=String)
    server_seq: Optional[int] = Field(
        default=None,
        description=(
            "WI-5. Set only on the 'changes_available' poke: the server's global "
            "sync high-water mark at emit time. Lets a local-first client skip a "
            "GET /api/changes pull when this seq is <= its stored cursor. Null on "
            "the legacy per-entity events."
        ),
    )
    target_user_ids: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(JSON),
        description=(
            "Explicit delivery targets (user ids as strings), captured at emit "
            "time. Used for events that destroy the rows target resolution relies "
            "on — deleting a checklist or revoking a collaborator — where resolving "
            "from live DB state would yield the wrong set (or nobody). When None, "
            "targets are resolved dynamically from owner + current collaborators."
        ),
    )
    target_tokens: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(JSON),
        description=(
            "Public-share tokens of connected anonymous SSE clients to deliver "
            "this notification to (in addition to target_user_ids). Lets live "
            "updates reach logged-out viewers. When None, resolved dynamically "
            "from the checklist's currently-active public links."
        ),
    )


class SyncNotificationPackage(BaseModel):
    target_user_ids: List[uuid.UUID]
    target_tokens: List[str] = []
    notification: SyncNotification
