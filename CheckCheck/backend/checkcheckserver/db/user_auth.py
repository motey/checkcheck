# Basics
from typing import AsyncGenerator, List, Optional, Literal, Sequence

# Libs
import enum
import uuid
import contextlib
from pydantic import SecretStr, Json
from fastapi import Depends, HTTPException, status
from sqlmodel import Field, select, delete, Enum, Column, and_, or_
from passlib.context import CryptContext
import secrets

# Internal
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel
from checkcheckserver.db._session import AsyncSession, get_async_session
from checkcheckserver.model.user import User
from checkcheckserver.model.user_auth import (
    UserAuth,
    UserAuthCreate,
    UserAuthUpdate,
    AllowedAuthSchemeType,
    crypt_context_pwd,
)
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface

log = get_logger()
config = Config()


class UserAuthCRUD(
    create_crud_base(
        table_model=UserAuth,
        read_model=UserAuth,
        create_model=UserAuthCreate,
        update_model=UserAuthUpdate,
    )
):
    async def list_by_user_id(
        self,
        user_id: str | uuid.UUID,
        filter_auth_source_type: AllowedAuthSchemeType = None,
        filter_oidc_provider_name: str = None,
        raise_exception_if_none: Exception = None,
        pagination: QueryParamsInterface = None,
    ) -> Sequence[UserAuth]:
        query = select(UserAuth).where(UserAuth.user_id == user_id)
        if filter_auth_source_type:
            query = query.where(UserAuth.auth_source_type == filter_auth_source_type)
        if filter_oidc_provider_name:
            query = query.where(
                UserAuth.oidc_provider_slug == filter_oidc_provider_name
            )
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        user_auths: Sequence[UserAuth] = results.all()
        if user_auths is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user_auths

    async def list_by_user_name(
        self,
        user_name: str | uuid.UUID,
        filter_auth_source_type: AllowedAuthSchemeType = None,
        filter_oidc_provider_name: str = None,
        raise_exception_if_none: Exception = None,
        pagination: QueryParamsInterface = None,
    ) -> Sequence[UserAuth]:
        query = select(UserAuth).join(User).where(User.user_name == user_name)
        if filter_auth_source_type:
            query = query.where(UserAuth.auth_source_type == filter_auth_source_type)
        if filter_oidc_provider_name:
            query = query.where(
                UserAuth.oidc_provider_slug == filter_oidc_provider_name
            )
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        user: UserAuth | None = results.all()
        if user is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user

    async def get_by_user_id_and_auth_source_and_provider_name(
        self,
        user_id: str | uuid.UUID,
        auth_source: AllowedAuthSchemeType,
        oidc_provider_slug: str = None,
        raise_exception_if_none: Exception = None,
    ) -> UserAuth | None:
        query = select(UserAuth).where(
            and_(
                UserAuth.user_id == user_id,
                UserAuth.auth_source_type == auth_source,
                UserAuth.oidc_provider_slug == oidc_provider_slug,
            )
        )
        results = await self.session.exec(statement=query)
        user_auth: UserAuth | None = results.one_or_none()
        if user_auth is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user_auth

    async def get_basic_auth_source_by_user_id(
        self, user_id: str | uuid.UUID, raise_exception_if_none: Exception = None
    ) -> UserAuth | None:
        query = select(UserAuth).where(
            and_(
                UserAuth.user_id == user_id,
                UserAuth.auth_source_type == AllowedAuthSchemeType.basic,
            )
        )
        results = await self.session.exec(statement=query)
        user_auth: UserAuth | None = results.one_or_none()

        if user_auth is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user_auth

    async def get_basic_auth_source_by_user_name(
        self, user_name: str, raise_exception_if_none: Exception = None
    ) -> UserAuth | None:
        query = select(User).where(User.user_name == user_name)
        results = await self.session.exec(statement=query)
        user: User | None = results.one_or_none()
        if not user and raise_exception_if_none:
            raise raise_exception_if_none
        return await self.get_basic_auth_source_by_user_id(
            user.id, raise_exception_if_none=raise_exception_if_none
        )

    async def get_api_token_by_id(
        self, token_id: str, raise_exception_if_none: Exception = None
    ) -> UserAuth | None:
        query = select(UserAuth).where(
            and_(
                UserAuth.auth_source_type == AllowedAuthSchemeType.api_token,
                UserAuth.api_token_id == token_id,
            )
        )
        results = await self.session.exec(statement=query)
        user_auth: UserAuth | None = results.one_or_none()
        if user_auth is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user_auth

    async def create(
        self,
        user_auth_create: UserAuthCreate,
        exists_ok: bool = False,
        custom_exception_if_basic_pw_auth_exists: Exception = None,
    ) -> UserAuth:

        if user_auth_create.auth_source_type == AllowedAuthSchemeType.basic:

            existing_user_basic_pw_auth = (
                await self.get_by_user_id_and_auth_source_and_provider_name(
                    user_id=user_auth_create.user_id,
                    auth_source=user_auth_create.auth_source_type,
                    oidc_provider_slug=user_auth_create.oidc_provider_slug,
                )
            )
            if existing_user_basic_pw_auth:
                if exists_ok:
                    return existing_user_basic_pw_auth
                raise (
                    custom_exception_if_basic_pw_auth_exists
                    if custom_exception_if_basic_pw_auth_exists
                    else ValueError(
                        "Basic Password Auth for user allready created. Please update the existing one."
                    )
                )

        user_auth = UserAuth.from_update_or_create_object(user_auth_create)
        self.session.add(user_auth)
        await self.session.commit()
        await self.session.refresh(user_auth)
        return user_auth

    async def delete(
        self, id: str | uuid.UUID, raise_exception_if_not_exists=None
    ) -> None | Literal[True]:
        user_auth = select(UserAuth).where(UserAuth.id == id)
        if user_auth is None and raise_exception_if_not_exists:
            raise raise_exception_if_not_exists
        else:
            query = delete(UserAuth).where(UserAuth.id == id)
            await self.session.exec(statement=query)
            await self.session.commit()
            return True

    async def update(
        self,
        user_auth_update: UserAuthUpdate,
        id_: str | uuid.UUID = None,
        custom_expception_if_not_exists: Exception = None,
    ) -> UserAuth:
        existing_user_auth = await self.session.get(UserAuth, id_)

        if not existing_user_auth:
            raise (
                custom_expception_if_not_exists
                if custom_expception_if_not_exists
                else ValueError(f"UserAuth with id {id_} not found")
            )
        existing_user_auth.update_secrets(user_auth_update)
        self.session.add(existing_user_auth)
        await self.session.commit()
        await self.session.refresh(existing_user_auth)
        return existing_user_auth
