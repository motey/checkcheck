"""Public URL share model (Phase 5 of card sharing).

A ``CheckListPublicShare`` is a *capability*: possession of its ``token`` grants
anonymous (logged-out) visitors access to a single checklist at the link's
``permission`` level (``view | check | edit`` — never ownership). Unlike a
``CheckListCollaborator`` it is not tied to a ``User``; the token itself is the
credential, so treat it like a password (high entropy, never logged, redacted in
list responses).

This replaces the broken ``CheckListExternalShare`` scaffold (which was never
wired into the schema).
"""

import datetime
import secrets
import uuid

from sqlmodel import Field, String

from checkcheckserver.model._base_model import TimestampedModel
from checkcheckserver.model.checklist_collaborator import SharePermission

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


def _generate_token() -> str:
    # ~256 bits of entropy; URL-safe so it can be dropped straight into a link.
    return secrets.token_urlsafe(32)


class CheckListPublicShareCreate(TimestampedModel, table=False):
    checklist_id: uuid.UUID = Field(
        foreign_key="checklist.id", ondelete="CASCADE", index=True
    )
    token: str = Field(
        default_factory=_generate_token,
        unique=True,
        index=True,
        description="URL-safe secret. This is the capability — never log it.",
    )
    permission: SharePermission = Field(
        default=SharePermission.view,
        sa_type=String,
        description="What an anonymous visitor holding this link may do.",
    )
    enabled: bool = Field(
        default=True,
        description="Soft on/off switch — disable a link without deleting it.",
    )
    expires_at: datetime.datetime | None = Field(
        default=None,
        description="Naive UTC expiry. Null = never expires.",
    )
    password_hash: str | None = Field(
        default=None,
        description=(
            "Optional bcrypt hash of a passphrase guarding this link. Null = no "
            "passphrase (the default). Never store or log the plaintext."
        ),
    )
    created_by: uuid.UUID = Field(foreign_key="user.id")


class CheckListPublicShare(CheckListPublicShareCreate, table=True):
    __tablename__ = "checklist_public_share"
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
    )
