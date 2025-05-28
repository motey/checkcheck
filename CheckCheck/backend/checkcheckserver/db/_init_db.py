from typing import AsyncGenerator, List, Optional, Awaitable, Any
import asyncio
from fastapi import Depends
from pathlib import Path, PurePath
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker
from alembic.config import Config as AlembicConfig
from alembic import command


from checkcheckserver.db._engine import db_engine
from checkcheckserver.db._session import get_async_session_context
from checkcheckserver.model.checklist import CheckList
from checkcheckserver.model.checklist_collaborator import CheckListCollaborator
from checkcheckserver.model.checklist_color_scheme import ChecklistColorScheme
from checkcheckserver.model.checklist_item import CheckListItem
from checkcheckserver.model.checklist_position import (
    CheckListPosition,
)
from checkcheckserver.db.user import (
    User,
    UserCRUD,
)
from checkcheckserver.db.user_auth import (
    UserAuth,
    UserAuthCreate,
    UserAuthCRUD,
    AllowedAuthSchemeType,
)
from checkcheckserver.db._db_data_provisioner import provision_data
from checkcheckserver.log import get_logger
from checkcheckserver.config import Config
from sqlalchemy.dialects.sqlite.aiosqlite import AsyncAdapt_aiosqlite_connection

log = get_logger()
config = Config()


# db_engine = create_async_engine(str(config.SQL_DATABASE_URL), echo=False, future=True)

from checkcheckserver.db._session import get_async_session
from sqlalchemy import event, Engine
from sqlite3 import Connection as SQLite3Connection


@event.listens_for(db_engine.sync_engine, "connect")
def enable_foreign_keys_on_sqlite(dbapi_connection, connection_record):
    """SQLlite databases disable foreign key contraints by default. This behaviour would prevents us from using things like cascade deletes.
    This addin enables that on every connect.

    Args:
        dbapi_connection (_type_): _description_
        connection_record (_type_): _description_
    """
    return
    # this is disabled for now, as sqlite does not support nullable composite keys (SIMPLE foreign key mode as defined in SQL-92 Standard)
    # we use nullable composite keys in the drug "Stamm"-model :(
    # instead we force optionally "PRAGMA foreign_keys=ON" on a per delete call base. see CheckCheck/backend/checkcheckserver/db/_base_crud.py - CRUDBase.delete()
    if isinstance(
        dbapi_connection,
        (
            SQLite3Connection,
            AsyncAdapt_aiosqlite_connection,
        ),
    ):
        log.debug("SQLite Database: Enable PRAGMA foreign_keys")
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


async def create_admin_if_not_exists(conn: AsyncConnection = None):
    # https://stackoverflow.com/questions/75150942/how-to-get-a-session-from-async-session-generator-fastapi-sqlalchemy
    # session = await anext(get_async_session())

    async with get_async_session_context() as session:
        async with UserCRUD.crud_context(session) as user_crud:
            user_crud: UserCRUD = user_crud
            admin_user = await user_crud.get_by_user_name(
                user_name=config.ADMIN_USER_NAME, show_deactivated=True
            )
        async with UserAuthCRUD.crud_context(session) as user_auth_crud:
            if admin_user is None:
                log.info(f"Creating admin user {config.ADMIN_USER_NAME}")
                admin_user = User(
                    user_name=config.ADMIN_USER_NAME,
                    email=config.ADMIN_USER_EMAIL,
                    deactivated=False,
                    roles=[config.ADMIN_ROLE_NAME],
                )
                admin_user = await user_crud.create(admin_user)
                admin_user_auth = UserAuthCreate(
                    user_id=admin_user.id,
                    basic_password=config.ADMIN_USER_PW.get_secret_value(),
                    auth_source_type=AllowedAuthSchemeType.basic,
                )
                log.info(f"admin_user_auth {admin_user_auth}")
                await user_auth_crud.create(admin_user_auth)


def init_alembic_migration_management():
    alembic_dir = Path(Path(__file__).parent.parent.parent, "migrations")

    alembic_cfg = AlembicConfig(config.DB_MIGRATION_ALEMBIC_CONFIG_FILE)
    alembic_cfg.set_main_option("sqlalchemy.url", str(config.SQL_DATABASE_URL))
    alembic_cfg.set_main_option("script_location", str(alembic_dir.resolve()))

    # alembic_cfg.attributes["connection"] = connection
    command.stamp(alembic_cfg, "head")


def run_alembic_database_migrations():
    alembic_dir = Path(Path(__file__).parent.parent.parent, "migrations")
    alembic_cfg = AlembicConfig(config.DB_MIGRATION_ALEMBIC_CONFIG_FILE)
    alembic_cfg.set_main_option("sqlalchemy.url", str(config.SQL_DATABASE_URL))
    alembic_cfg.set_main_option("script_location", str(alembic_dir.resolve()))
    command.upgrade(alembic_cfg, "head")


async def create_schema(conn: AsyncConnection):
    await conn.run_sync(SQLModel.metadata.create_all)


async def _async_db_init_task(task: Awaitable) -> Any:
    async with db_engine.begin() as conn:
        return await task(conn)


def _run_async_db_init_task(task: Awaitable) -> Any:

    return asyncio.run(_async_db_init_task(task))


def init_db():
    log.info(f"Create tables if not existent {config.SQL_DATABASE_URL}")
    _run_async_db_init_task(create_schema)
    log.info(f"Init database migrations if neccessary")
    init_alembic_migration_management()
    log.info(f"Run database migrations if neccessary")
    run_alembic_database_migrations()
    # log.info(f"Create admin if not existent")
    _run_async_db_init_task(create_admin_if_not_exists)
    log.info(f"Insert DB provisioning data if neccessary")
    _run_async_db_init_task(provision_data)
