from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import event, Engine
from sqlite3 import Connection as SQLite3Connection
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger

config = Config()
log = get_logger()


db_engine = create_async_engine(
    str(config.SQL_DATABASE_URL), echo=config.DEBUG_SQL, future=True
)
