from typing import Dict, List, Callable, Awaitable
from contextlib import asynccontextmanager
import getversion
import inspect
from fastapi import Depends
from fastapi import FastAPI
import getversion.plugin_setuptools_scm
from starlette.middleware.sessions import SessionMiddleware
from fastapi.middleware.cors import CORSMiddleware
from checkcheckserver.api.routers_map import mount_fast_api_routers
from pathlib import Path
import json
from fastapi.openapi.utils import get_openapi

# from fastapi.security import

import checkcheckserver
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()

from dataclasses import dataclass


@dataclass
class AppLifespanCallback:
    func: Callable
    params: Dict | None = None

    def is_async(self):
        return inspect.iscoroutinefunction(self.func)


class FastApiAppContainer:
    def __init__(self):
        self.shutdown_callbacks: List[AppLifespanCallback] = []
        self.startup_callbacks: List[AppLifespanCallback] = []
        self.app = FastAPI(
            title="CheckCheck REST API",
            version=getversion.get_module_version(checkcheckserver)[0],
            # openapi_url=f"{settings.api_v1_prefix}/openapi.json",
            # debug=settings.debug,
            lifespan=self._app_lifespan,
        )
        self._mount_routers()
        self._apply_api_middleware()

    def add_startup_callback(self, func: Callable, params: Dict | None = None):
        self.startup_callbacks.append(AppLifespanCallback(func=func, params=params))

    def add_shutdown_callback(self, func: Callable, params: Dict | None = None):
        self.shutdown_callbacks.append(AppLifespanCallback(func=func, params=params))

    def dump_open_api_specification(self, json_file_path: Path):

        if json_file_path.suffix.upper() not in [".JSON"]:
            json_file_path = Path(json_file_path, "openapi.json")
        json_parent_dir_path = json_file_path.parent
        json_parent_dir_path.mkdir(exist_ok=True, parents=True)
        # f"{Path(__file__).parent}/../../openapi.json"
        with open(json_file_path, "w") as f:
            json.dump(
                get_openapi(
                    title=self.app.title,
                    version=self.app.version,
                    openapi_version=self.app.openapi_version,
                    description=self.app.description,
                    routes=self.app.routes,
                ),
                f,
            )

    @asynccontextmanager
    async def _app_lifespan(self, app: FastAPI):
        # https://fastapi.tiangolo.com/advanced/events/#lifespan
        for cb in self.startup_callbacks:
            params = cb.params if cb.params else {}
            if cb.is_async():
                await cb.func(**params)
            else:
                cb.func(**params)

        yield
        for cb in self.shutdown_callbacks:
            params = cb.params if cb.params else {}
            if cb.is_async():
                await cb.func(**params)
            else:
                cb.func(**params)

    def _apply_api_middleware(self):
        allow_origins = []
        for oidc_config in config.AUTH_OIDC_PROVIDERS:
            if oidc_config.ENABLED:
                allow_origins.append(
                    str(oidc_config.CONFIGURATION_ENDPOINT)
                    .replace("//", "##")
                    .split("/", 1)[0]
                    .replace("##", "//")
                )

        allow_origins.extend(
            [
                str(config.CLIENT_URL).rstrip("/"),
                str(config.get_server_url()).rstrip("/"),
            ]
        )
        allow_origins = set(allow_origins)
        log.info(f"Origin allowed: {allow_origins}")
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=set(allow_origins),
            allow_methods=["*"],
            allow_headers=["*"],
            allow_credentials=True,
        )
        self.app.add_middleware(
            SessionMiddleware,
            secret_key=config.SERVER_SESSION_SECRET.get_secret_value(),
        )

    def _mount_routers(self):
        mount_fast_api_routers(self.app)
