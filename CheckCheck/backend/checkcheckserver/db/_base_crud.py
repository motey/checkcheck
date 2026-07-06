from typing import (
    List,
    Optional,
    Sequence,
    Type,
    AsyncGenerator,
    Self,
    TypeVar,
    Generic,
    ClassVar,
    Any,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy import text
from fastapi import Depends
import contextlib
from sqlmodel.ext.asyncio.session import AsyncSession
from checkcheckserver.api.paginator import QueryParamsInterface
from checkcheckserver.config import Config as _Config, DbBackend as _DbBackend
_db_backend = _Config().db_backend
from sqlmodel import func, select, delete, col
from uuid import UUID

from checkcheckserver.db._session import get_async_session

from checkcheckserver.model._base_model import (
    BaseTable,
    TimestampedModel,
    SoftDeleteMixin,
    naive_utc_now,
)

from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

log = get_logger()
config = Config()


GenericCRUDTableType = TypeVar("GenericCRUDTableType", bound=BaseTable)
GenericCRUDReadType = TypeVar("GenericCRUDReadType", bound=BaseTable)
GenericCRUDUpdateType = TypeVar("GenericCRUDUpdateType", bound=BaseTable)
GenericCRUDCreateType = TypeVar("GenericCRUDCreateType", bound=BaseTable)


class CRUDBaseMetaClass(type):
    def __new__(mcs, name, bases, namespace, **kwargs):
        cls = super().__new__(mcs, name, bases, namespace, **kwargs)

        # Cache the generic type arguments for better type resolution
        # We need to check this after CRUDBase is defined, so we'll do it in __init_subclass__
        return cls

    @property
    def crud_context(cls):
        """Async context manager for CRUD operations"""
        return contextlib.asynccontextmanager(cls.get_crud)


class DatabaseInteractionBase(metaclass=CRUDBaseMetaClass):
    def __init__(self, session: AsyncSession):
        self.session = session

    @classmethod
    async def get_crud(
        cls,
        session: AsyncSession = Depends(get_async_session),
    ) -> AsyncGenerator[Self, None]:
        yield cls(session=session)


class CRUDBase(
    DatabaseInteractionBase,
    Generic[
        GenericCRUDTableType,
        GenericCRUDReadType,
        GenericCRUDCreateType,
        GenericCRUDUpdateType,
    ],
):
    # These will be set by __init_subclass__
    _table_cls: ClassVar[Type[Any]]
    _read_cls: ClassVar[Type[Any]]
    _create_cls: ClassVar[Type[Any]]
    _update_cls: ClassVar[Type[Any]]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        # Cache the generic type arguments for better type resolution
        if hasattr(cls, "__orig_bases__"):
            for base in cls.__orig_bases__:
                if (
                    hasattr(base, "__origin__")
                    and base.__origin__ is CRUDBase
                    and len(base.__args__) == 4
                ):
                    cls._table_cls = base.__args__[0]
                    cls._read_cls = base.__args__[1]
                    cls._create_cls = base.__args__[2]
                    cls._update_cls = base.__args__[3]
                    break

    @classmethod
    def get_table_cls(cls) -> Type[GenericCRUDTableType]:
        if hasattr(cls, "_table_cls"):
            return cls._table_cls
        # Fallback to original method
        for base in getattr(cls, "__orig_bases__", []):
            if (
                hasattr(base, "__origin__")
                and base.__origin__ is CRUDBase
                and len(base.__args__) == 4
            ):
                return base.__args__[0]
        raise RuntimeError(f"Cannot determine table class for {cls}")

    @classmethod
    def get_read_cls(cls) -> Type[GenericCRUDReadType]:
        if hasattr(cls, "_read_cls"):
            return cls._read_cls
        for base in getattr(cls, "__orig_bases__", []):
            if (
                hasattr(base, "__origin__")
                and base.__origin__ is CRUDBase
                and len(base.__args__) == 4
            ):
                return base.__args__[1]
        raise RuntimeError(f"Cannot determine read class for {cls}")

    @classmethod
    def get_create_cls(cls) -> Type[GenericCRUDCreateType]:
        if hasattr(cls, "_create_cls"):
            return cls._create_cls
        for base in getattr(cls, "__orig_bases__", []):
            if (
                hasattr(base, "__origin__")
                and base.__origin__ is CRUDBase
                and len(base.__args__) == 4
            ):
                return base.__args__[2]
        raise RuntimeError(f"Cannot determine create class for {cls}")

    @classmethod
    def get_update_cls(cls) -> Type[GenericCRUDUpdateType]:
        if hasattr(cls, "_update_cls"):
            return cls._update_cls
        for base in getattr(cls, "__orig_bases__", []):
            if (
                hasattr(base, "__origin__")
                and base.__origin__ is CRUDBase
                and len(base.__args__) == 4
            ):
                return base.__args__[3]
        raise RuntimeError(f"Cannot determine update class for {cls}")

    @classmethod
    def get_default_order_by(cls):
        pass

    @classmethod
    def _is_soft_deletable(cls) -> bool:
        """Whether this CRUD's table carries a ``deleted_at`` tombstone column
        (WI-2). Drives the automatic ``deleted_at IS NULL`` masking in the generic
        read paths so no route can accidentally surface a tombstoned row."""
        return issubclass(cls.get_table_cls(), SoftDeleteMixin)

    ################
    # CRUD METHODS #
    ################

    async def count(self) -> int:
        query = select(func.count()).select_from(self.get_table_cls())
        results = await self.session.exec(statement=query)
        return results.first()

    async def list(
        self,
        pagination: QueryParamsInterface = None,
        include_deleted: bool = False,
    ) -> Sequence[GenericCRUDReadType]:
        tbl = self.get_table_cls()
        query = select(tbl)
        if self._is_soft_deletable() and not include_deleted:
            query = query.where(col(tbl.deleted_at).is_(None))
        if pagination:
            query = pagination.append_to_query(query)
        results = await self.session.exec(statement=query)
        return results.all()

    async def _get(
        self,
        id_: str | UUID,
        raise_exception_if_none: Exception = None,
        include_deleted: bool = False,
    ) -> Optional[GenericCRUDReadType]:
        # get() could be overwritten in  a child class that why we create an internal _get() function that can be used by other funcs like update()
        if isinstance(id_, str):
            id_ = UUID(id_)
        result = await self.session.get(self.get_table_cls(), id_)
        # Mask tombstoned rows (WI-2): a soft-deleted row reads as "not found"
        # everywhere except explicit tombstone-aware paths (delta feed, the 410
        # guards). Callers that must distinguish "gone" from "never existed" pass
        # include_deleted=True and inspect deleted_at themselves.
        if (
            result is not None
            and not include_deleted
            and self._is_soft_deletable()
            and result.deleted_at is not None
        ):
            result = None
        if result is None and raise_exception_if_none:
            raise raise_exception_if_none
        return result

    async def get(
        self,
        id_: str | UUID,
        raise_exception_if_none: Exception = None,
        include_deleted: bool = False,
    ) -> Optional[GenericCRUDReadType]:

        return await self._get(
            id_, raise_exception_if_none, include_deleted=include_deleted
        )

    async def get_multiple(
        self,
        ids: List[UUID],
        raise_exception_if_objects_missing: Exception = None,
    ) -> List[GenericCRUDReadType]:
        log.debug(f"get multiple ids: {ids}")
        log.debug(f"self.get_table_cls().id: {self.get_table_cls().id}")
        if ids:
            query = select(self.get_table_cls()).where(
                col(self.get_table_cls().id).in_(ids)
            )
            results = await self.session.exec(statement=query)
            res = results.all()
            if len(res) != len(ids) and raise_exception_if_objects_missing:
                raise raise_exception_if_objects_missing
            return res
        return []

    async def create(
        self,
        obj: GenericCRUDCreateType,
        exists_ok: bool = False,
        raise_custom_exception_if_exists: Exception = None,
    ) -> GenericCRUDReadType:

        if exists_ok and raise_custom_exception_if_exists:
            log.warning(
                f"'exists_ok' and 'raise_custom_exception_if_exists' are set for creation of '{type(obj)}'. If object exists and 'exists_ok' = True,  it will never raise the Exception."
            )
        new_obj: GenericCRUDTableType = self.get_table_cls().model_validate(
            obj.model_dump()
        )

        self.session.add(new_obj)
        try:
            await self.session.commit()
        except IntegrityError as err:
            err_str = str(err)
            is_unique_violation = (
                "UNIQUE constraint failed" in err_str          # SQLite
                or "UniqueViolationError" in err_str           # PostgreSQL/asyncpg
                or "duplicate key value violates unique" in err_str  # PostgreSQL direct
            )
            if is_unique_violation and exists_ok:
                log.debug(
                    f"Object of object of type '{type(obj)}' already exists. Skipping creation. <{obj}>. If this is called very often consider creating a custom create function for '{type(self)}'"
                )
                await self.session.rollback()
                return await self.find(
                    obj,
                    raise_exception_if_more_than_one_result=ValueError(
                        f"Failed retrieving existing <{self.get_table_cls()}> object based on <{obj}>. Given attributes are inconclusive and result in multiple possible results."
                    ),
                )
            else:
                if raise_custom_exception_if_exists:
                    raise raise_custom_exception_if_exists
                raise err

        await self.session.refresh(new_obj)
        log.debug(
            f"Created {self.get_table_cls().__name__} object of type '{type(obj)}'.\n\tData In: <{obj}>\n\tData Out: <{new_obj}>"
        )
        return new_obj

    async def find(
        self,
        obj: GenericCRUDReadType | GenericCRUDUpdateType | GenericCRUDCreateType,
        raise_exception_if_not_exists: Exception = None,
        raise_exception_if_more_than_one_result: Exception = None,
    ) -> Sequence[GenericCRUDReadType]:
        """Find matching objects in the database, based on the attributes in the given "obj"

        Args:
            obj (GenericCRUDReadType): _description_
            raise_exception_if_not_exists (Exception, optional): _description_. Defaults to None.
        """
        tbl = self.get_table_cls()
        query = select(tbl)
        if self._is_soft_deletable():
            # find() backs the exists_ok create path; a tombstoned row must not be
            # returned as an "existing" match (WI-2). Re-creating over a tombstone
            # is WI-3 territory.
            query = query.where(col(tbl.deleted_at).is_(None))

        for attr, val in obj.model_dump().items():
            query = query.where(getattr(tbl, attr) == val)
        # log.debug(f"find {tbl.__class__} query: {query}")
        res = await self.session.exec(query)
        result_objs = list(res.unique())
        # log.debug(("result_objs", list(result_objs)))
        if len(result_objs) == 0 and raise_exception_if_not_exists:
            raise raise_exception_if_not_exists
        elif len(result_objs) > 1 and raise_exception_if_more_than_one_result:
            raise raise_exception_if_more_than_one_result
        return result_objs

    async def update(
        self,
        update_obj: GenericCRUDUpdateType | GenericCRUDTableType,
        id_: str | UUID = None,
        raise_exception_if_not_exists=None,
    ) -> GenericCRUDReadType:
        id_ = id_ if id_ is not None else getattr(update_obj, "id", None)
        if id_ is None:
            raise ValueError("No id_ (primary key) provided. Could not update")
        # replace with query or internal get, as get could be overwriten by child class
        obj_from_db = await self._get(
            id_=id_, raise_exception_if_none=raise_exception_if_not_exists
        )
        for k, v in update_obj.model_dump(exclude_unset=True).items():
            # Never repoint the primary key through an update body (WI-3): some
            # update schemas inherit an optional client-supplied `id` from their
            # create schema, and a replayed/hostile PATCH must not move the row.
            if k == "id":
                continue
            if k in self.get_update_cls().model_fields.keys():
                setattr(obj_from_db, k, v)
        # Explicitly stamp the sync version signal. The `before_update` mapper
        # event only fires when the flush emits an UPDATE, so an update that
        # dirties no other column would otherwise leave `updated_at` stale;
        # setting it here also marks the row dirty so the bump always persists.
        if isinstance(obj_from_db, TimestampedModel):
            obj_from_db.updated_at = naive_utc_now()
        self.session.add(obj_from_db)
        await self.session.commit()
        await self.session.refresh(obj_from_db)
        return obj_from_db

    async def delete(
        self,
        id_: str | UUID,
        raise_exception_if_not_exists=None,
        force_pragma_foreign_keys: bool = False,
    ):
        tbl = self.get_table_cls()
        existing_obj = await self._get(id_, raise_exception_if_not_exists)
        if existing_obj is not None:
            del_statement = delete(tbl).where(tbl.id == id_)
            if force_pragma_foreign_keys and _db_backend == _DbBackend.SQLITE:
                await self.session.exec(text("PRAGMA foreign_keys = ON;"))
            await self.session.exec(del_statement)
            await self.session.commit()
        return

    async def soft_delete(
        self,
        id_: str | UUID,
        raise_exception_if_not_exists: Exception = None,
    ) -> Optional[GenericCRUDReadType]:
        """Tombstone a syncable parent row instead of DELETE-ing it (WI-2).

        Sets ``deleted_at`` so the removal reaches offline clients via the delta
        feed and a stale edit cannot resurrect the row. Only the parent row is
        touched — child rows (item state/position, links) stay and are masked by
        this tombstone. Idempotent: re-deleting keeps the original timestamp and
        does not error, so an outbox replay of a delete is safe.
        """
        if not self._is_soft_deletable():
            raise TypeError(
                f"{self.get_table_cls().__name__} has no deleted_at column; "
                "soft_delete is only valid for tombstoned tables."
            )
        obj = await self._get(
            id_,
            raise_exception_if_none=raise_exception_if_not_exists,
            include_deleted=True,
        )
        if obj is None:
            return None
        if obj.deleted_at is None:
            obj.deleted_at = naive_utc_now()
            self.session.add(obj)
            await self.session.commit()
            await self.session.refresh(obj)
        return obj

    async def create_bulk(
        self,
        objects: List[GenericCRUDCreateType],
    ):
        log.debug(f"Create bulk of {self.get_table_cls().__name__}")
        for obj in objects:
            if not isinstance(obj, self.get_create_cls()):
                raise ValueError(
                    f"List item is not a {self.get_table_cls().__name__} instance:\n {obj}"
                )
        self.session.add_all(objects)
        await self.session.commit()


def create_crud_base(
    table_model: BaseTable,
    read_model: BaseTable,
    create_model: BaseTable,
    update_model: BaseTable,
) -> Type[CRUDBase]:
    return CRUDBase[table_model, read_model, create_model, update_model]
