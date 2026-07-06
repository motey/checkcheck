from typing import Optional
import asyncio
from pathlib import Path
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy import text
from alembic.config import Config as AlembicConfig
from alembic import command

from checkcheckserver.db._engine import db_engine
from checkcheckserver.db._session import get_async_session_context
from checkcheckserver.db.user import User, UserCRUD
from checkcheckserver.db.user_auth import (
    UserAuthCreate,
    UserAuthCRUD,
    AllowedAuthSchemeType,
)
from checkcheckserver.db._db_data_provisioner import provision_data
from checkcheckserver.model._base_model import SYNC_SEQ_ROW_ID, SyncSequence
from checkcheckserver.log import get_logger
from checkcheckserver.config import Config

log = get_logger()
config = Config()


async def _get_current_alembic_revision(conn: AsyncConnection) -> str | None:
    try:
        result = await conn.execute(
            text("SELECT version_num FROM alembic_version LIMIT 1")
        )
        row = result.fetchone()
        return row[0] if row else None
    except Exception:
        return None


def init_schema_and_migrations():
    """Synchronous. Call before the main event loop.

    Creates all tables (create_all for fresh databases) and runs alembic
    migrations. Uses its own short-lived asyncio.run() for create_all, disposing
    the pool inside that loop so the main event loop starts with clean connections.
    """
    async def _create_schema_and_check_rev():
        async with db_engine.begin() as conn:
            await conn.run_sync(SQLModel.metadata.create_all)
            # Seed the WI-4 global sync-sequence counter (a single row) if absent.
            # Must exist before the first syncable write, whose before_insert event
            # increments it. ON CONFLICT DO NOTHING keeps a persisted dev DB's
            # existing high-water mark; both SQLite and Postgres accept this form.
            await conn.execute(
                text(
                    "INSERT INTO sync_seq (id, value) VALUES (:row_id, 0) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"row_id": SYNC_SEQ_ROW_ID},
            )
        async with db_engine.connect() as conn:
            rev = await _get_current_alembic_revision(conn)
        # Dispose inside the event loop — asyncpg requires async close and
        # will log MissingGreenlet errors if disposed via sync_engine.dispose().
        await db_engine.dispose()
        return rev

    log.info(f"Create tables if not existent: {config.SQL_DATABASE_URL}")
    current_rev = asyncio.run(_create_schema_and_check_rev())

    alembic_dir = Path(Path(__file__).parent.parent.parent, "migrations")
    alembic_cfg = AlembicConfig(config.DB_MIGRATION_ALEMBIC_CONFIG_FILE)
    alembic_cfg.set_main_option("sqlalchemy.url", str(config.SQL_DATABASE_URL))
    alembic_cfg.set_main_option("script_location", str(alembic_dir.resolve()))

    log.info("Init database migrations if necessary")
    if current_rev is None:
        # Fresh database — schema already created by create_all above;
        # stamp so upgrade doesn't try to re-apply already-implicit migrations.
        command.stamp(alembic_cfg, "head")

    log.info("Run database migrations if necessary")
    command.upgrade(alembic_cfg, "head")


async def init_db():
    """Async. Must run inside the same event loop as uvicorn.

    Creates the admin user and provisions seed data. Runs after
    init_schema_and_migrations() so all tables and migrations are in place.
    """
    log.info("Creating admin user if not exists")
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
                await user_auth_crud.create(admin_user_auth)

    log.info("Insert DB provisioning data if necessary")
    await provision_data()
