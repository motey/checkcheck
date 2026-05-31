import sys, os
from typing import Dict, List, Optional
import logging
import os
import getversion
import yaml
import sys
import asyncio
import json
import argparse
from fastapi.responses import FileResponse
from fastapi.openapi.utils import get_openapi
import json
from pathlib import Path

# Setup logging
log = logging.getLogger(__name__)
log.addHandler(logging.StreamHandler(sys.stdout))


# Add CheckCheckServer to global Python modules.
# This way we address checkcheckserver as a module for imports without installing it first.
# e.g. "from checkcheckserver import config"
if __name__ == "__main__":

    MODULE_DIR = Path(__file__).parent
    MODULE_PARENT_DIR = MODULE_DIR.parent.absolute()
    sys.path.insert(0, os.path.normpath(MODULE_PARENT_DIR))

# Import and load config
from checkcheckserver.config import Config

config = Config()


def start():
    import checkcheckserver

    from checkcheckserver.log import (
        get_logger,
        get_loglevel,
        get_uvicorn_loglevel,
        APP_LOGGER_DEFAULT_NAME,
    )

    log = get_logger()
    try:
        version = getversion.get_module_version(checkcheckserver)[0]
    except:
        version = "unkown"
    print(f"Start checkcheckserver version: {version}")

    log.debug("----CONFIG-----")
    log.debug(yaml.dump(json.loads(config.model_dump_json()), sort_keys=False))
    log.debug("----CONFIG-END-----")
    # test_exporter()
    # exit()
    print(f"LOG_LEVEL: {config.LOG_LEVEL}")
    print(f"UVICORN_LOG_LEVEL: {get_uvicorn_loglevel()}")
    print(
        f"allow_origins=[{config.CLIENT_URL}, {str(config.get_server_url()).rstrip('/')}]"
    )

    from checkcheckserver.db._init_db import init_schema_and_migrations, init_db
    import uvicorn
    from uvicorn.config import LOGGING_CONFIG
    from checkcheckserver.app import FastApiAppContainer

    app_container = FastApiAppContainer()

    uvicorn_log_config: Dict = LOGGING_CONFIG
    uvicorn_log_config["loggers"][APP_LOGGER_DEFAULT_NAME] = {
        "handlers": ["default"],
        "level": get_loglevel(),
    }
    app_container.dump_open_api_specification(
        Path(Path(__file__).parent, "../../openapi.json")
    )

    # Sync phase: create schema + run alembic migrations before the event loop.
    # This disposes the engine pool so uvicorn's event loop gets clean connections.
    init_schema_and_migrations()

    uvicorn_config = uvicorn.Config(
        app=app_container.app,
        host=config.SERVER_LISTENING_HOST,
        port=config.SERVER_LISTENING_PORT,
        log_level=get_uvicorn_loglevel(),
        log_config=uvicorn_log_config,
        proxy_headers=True,
    )
    uvicorn_server = uvicorn.Server(config=uvicorn_config)

    # Async phase: admin/provisioning + uvicorn all share one event loop so
    # asyncpg connections are never reused across loop boundaries.
    async def _run():
        await init_db()
        await uvicorn_server.serve()

    asyncio.run(_run())


if __name__ == "__main__":
    start()
