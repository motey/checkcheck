from typing import AsyncGenerator, List, Optional, Literal, Sequence, Annotated
from pydantic import validate_email, validator, StringConstraints, model_validator
from typing import Optional
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select, delete, func, and_, or_
from uuid import UUID


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model.user import (
    User,
    UserUpdate,
    UserUpdateByAdmin,
    UserUpdateByUser,
    UserCreate,
)
from checkcheckserver.db._base_crud import create_crud_base
from checkcheckserver.api.paginator import QueryParamsInterface


log = get_logger()
config = Config()


class UserCRUD(
    create_crud_base(
        table_model=User,
        read_model=User,
        create_model=UserCreate,
        update_model=UserUpdate,
    )
):
    async def count(
        self,
        show_deactivated: bool = False,
    ) -> int:
        query = select(func.count()).select_from(User)
        if not show_deactivated:
            query = query.where(User.deactivated == False)
        results = await self.session.exec(statement=query)
        return results.first()

    async def list(
        self, show_deactivated: bool = False, pagination: QueryParamsInterface = None
    ) -> Sequence[User]:
        query = select(User)
        if not show_deactivated:
            query = query.where(User.deactivated == False)
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def get(
        self,
        user_id: str | UUID,
        show_deactivated: bool = False,
        raise_exception_if_none: Exception = None,
    ) -> Optional[User]:
        query = select(User).where(User.id == user_id)
        if not show_deactivated:
            query = query.where(User.deactivated == False)

        results = await self.session.exec(statement=query)
        user: User | None = results.one_or_none()
        if user is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user

    async def create(
        self,
        obj: UserCreate,
        exists_ok: bool = False,
        raise_custom_exception_if_exists: Exception = None,
    ) -> User:
        existing_user_query = select(User).where(User.user_name == obj.user_name)

        existing_user_result = await self.session.exec(statement=existing_user_query)
        existing_user: User | None = existing_user_result.one_or_none()
        if existing_user is not None:
            if exists_ok:
                return existing_user
            elif raise_custom_exception_if_exists:
                raise raise_custom_exception_if_exists
            else:
                raise ValueError("Username exists. Can not create user.")
        user = User.model_validate(obj)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        from checkcheckserver.db._hooks.create_user import (
            create_new_user_default_labels,
        )

        await create_new_user_default_labels(self.session, user.id)

        return user

    async def get_by_user_name(
        self,
        user_name: str,
        show_deactivated: bool = False,
        raise_exception_if_none: Exception = None,
    ) -> Optional[User]:
        if show_deactivated:
            query = select(User).where(User.user_name == user_name)
        else:
            query = select(User).where(
                and_(User.user_name == user_name, User.deactivated == False)
            )
        results = await self.session.exec(statement=query)
        user: User | None = results.one_or_none()
        if user is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user

    async def get_by_user_name_or_email(
        self,
        user_name_or_email: str,
        include_deactivated: bool = False,
        raise_exception_if_none: Exception = None,
    ) -> Optional[User]:
        log.debug(f"user_name_or_email {user_name_or_email}")
        query = select(User)
        if "@" in user_name_or_email:

            query = query.where(
                User.email == user_name_or_email,
            )
        else:
            query = query.where(
                User.user_name == user_name_or_email,
            )

        if include_deactivated != True:
            query = query.where(User.deactivated == False)

        results = await self.session.exec(statement=query)
        user: User | None = results.one_or_none()

        if user is None and raise_exception_if_none:
            raise raise_exception_if_none
        return user

    async def disable(
        self,
        user_id: str | UUID,
        raise_exception_if_not_exists=None,
        raise_exception_if_allready_deactivated=None,
    ) -> bool:
        if user_id is None:
            raise ValueError("No user_id provided")
        user = await self.get(
            user_id=user_id,
            raise_exception_if_none=raise_exception_if_not_exists,
            show_deactivated=True,
        )
        if user.deactivated and raise_exception_if_allready_deactivated:
            raise raise_exception_if_allready_deactivated
        user.deactivated = True
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)

        # for good measure disable all refresh tokens as well
        # TODO: this generated a circular import we need to seperate model and crud classes
        # UserAuthRefreshTokenCRUD(self.session).disable_by_user_id(user_id=user_id)
        return user

    async def update(
        self,
        user_update: UserUpdate | UserUpdateByUser | UserUpdateByAdmin,
        user_id: str | UUID = None,
        raise_exception_if_not_exists=None,
    ) -> User:
        user_id = user_id if user_id else user_update.id
        if user_id is None:
            raise ValueError(
                "User update failed, uuid must be set in user_update or passed as argument `id`"
            )
        user_from_db = await self.get(
            user_id=user_id,
            raise_exception_if_none=raise_exception_if_not_exists,
            show_deactivated=True,
        )
        for k, v in user_update.model_dump(exclude_unset=True).items():
            if k in UserUpdate.model_fields.keys():
                setattr(user_from_db, k, v)
        self.session.add(user_from_db)
        await self.session.commit()
        await self.session.refresh(user_from_db)
        return user_from_db
