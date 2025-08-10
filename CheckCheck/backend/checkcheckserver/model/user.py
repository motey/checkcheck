from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Self,
    Dict,
)
from pydantic import (
    EmailStr,
    StringConstraints,
    model_validator,
    field_validator,
)
from pydantic_core import PydanticCustomError
from fastapi import Depends
import contextlib
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import Field, select, delete, Column, JSON, SQLModel

import uuid
from uuid import UUID

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import BaseTable, TimestampedModel
from checkcheckserver.model.user_info_oidc import UserInfoOidc

log = get_logger()
config = Config()


class UserBase(BaseTable, table=False):
    email: Optional[str] = Field(
        default=None,
        index=True,
        max_length=320,
        schema_extra={"examples": ["clara@uni.wroc.pl", "titor@time.com"]},
    )
    display_name: Optional[str] = Field(
        default=None,
        max_length=128,
        min_length=2,
        schema_extra={"examples": ["Clara Immerwahr", "John Titor"]},
    )


class UserRegisterAPI(UserBase, table=False):
    email: EmailStr = Field(
        schema_extra={"examples": ["clara@uni.wroc.pl", "titor@time.com"]},
    )
    user_name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            to_lower=True,
            pattern=r"^[a-zA-Z0-9.-]+$",
            max_length=128,
            min_length=3,
        ),
    ] = Field(
        index=True,
        unique=True,
        schema_extra={"examples": ["clara.immerwahr", "titor.extern.times"]},
    )


class UserUpdateByUser(UserBase, table=False):
    pass


class UserUpdate(UserBase, table=False):
    id: Optional[uuid.UUID] = Field(default=None)


class UserUpdateByAdmin(UserUpdate, table=False):
    roles: List[str] = Field(default=[], sa_column=Column(JSON))
    deactivated: bool = Field(default=False)
    is_email_verified: bool = Field(default=False)

    def is_admin(self):
        if config.ADMIN_ROLE_NAME in self.roles:
            return True
        return False

    def is_usermanager(self):
        if config.USERMANAGER_ROLE_NAME in self.roles or self.is_admin():
            return True
        return False


class _UserWithName(UserBase, table=False):
    user_name: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True,
            to_lower=True,
            pattern=r"^[a-zA-Z0-9.-]+$",
            max_length=128,
            min_length=3,
        ),
    ] = Field(
        index=True,
        unique=True,
        schema_extra={"examples": ["clara.immerwahr", "titor.extern.times"]},
    )

    @model_validator(mode="before")
    @classmethod
    def val_display_name(self, values):
        """if no display name is set for now, we copy the identifying `user_name`"""
        # print("values", type(values), values)

        if isinstance(values, dict) and (
            "display_name" not in values or values["display_name"] is None
        ):
            values["display_name"] = values["user_name"]

        if isinstance(values, self) and self.display_name is None:
            # print("self.user_name", type(self.user_name), self.user_name)
            self.display_name = self.user_name
        return values


class UserCreate(_UserWithName, UserUpdateByAdmin, table=False):
    id: Optional[uuid.UUID] = Field(default_factory=uuid.uuid4)

    @classmethod
    def from_oidc_userinfo(cls, oidc_userinfo: UserInfoOidc) -> Self:
        oidc_provider_config = next(
            oidc_config
            for oidc_config in config.AUTH_OIDC_PROVIDERS
            if oidc_config.get_provider_name_slug() == oidc_userinfo.provider_slug
        )
        user_name = oidc_userinfo.preferred_username
        if oidc_provider_config.PREFIX_USERNAME_WITH_PROVIDER_SLUG:
            user_name = f"{oidc_userinfo.provider_slug}_{user_name}"
        available_medlog_roles = [config.ADMIN_ROLE_NAME, config.USERMANAGER_ROLE_NAME]
        roles = [r for r in oidc_userinfo.groups if r in available_medlog_roles]
        if oidc_provider_config.ROLE_MAPPING:
            for (
                oidc_mapping_group,
                mapping_roles,
            ) in oidc_provider_config.ROLE_MAPPING.items():
                if oidc_mapping_group in oidc_userinfo.groups:
                    roles.extend(mapping_roles)

        userdata = {}
        userdata["user_name"] = user_name
        userdata["display_name"] = oidc_userinfo.name
        userdata["email"] = oidc_userinfo.email
        userdata["roles"] = roles
        return cls(**userdata)


class User(_UserWithName, UserUpdateByAdmin, TimestampedModel, table=True):
    __tablename__ = "user"
    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        # sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
