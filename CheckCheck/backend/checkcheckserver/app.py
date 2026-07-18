from typing import Dict, List, Callable, Awaitable
from contextlib import asynccontextmanager
import getversion
import inspect
from fastapi import Depends
from fastapi import FastAPI
import getversion.plugin_setuptools_scm
from starlette.middleware.sessions import SessionMiddleware
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send
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


class APINoStoreCacheMiddleware:
    """Stamp ``Cache-Control: no-store`` on every ``/api/*`` response.

    API replies are dynamic and must never be reused from a browser (or proxy)
    cache — otherwise a stale JSON body can be served after a deploy (e.g. an old
    ``server_version`` from ``/api/public-config``). FastAPI sends no cache headers
    by default, which leaves the response *eligible* for heuristic caching; this
    closes that door explicitly.

    Pure-ASGI (not ``BaseHTTPMiddleware``) so it never buffers the body — the
    long-lived ``/api/sync`` SSE stream passes straight through, headers stamped
    once on ``http.response.start``. ``setdefault`` leaves any endpoint that sets
    its own ``Cache-Control`` untouched.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_with_no_store(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message["headers"])
                headers.setdefault("cache-control", "no-store")
            await send(message)

        await self.app(scope, receive, send_with_no_store)


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
                sort_keys=False,
                indent=2,
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
        # Prevent any browser/proxy from reusing a dynamic API reply from cache.
        self.app.add_middleware(APINoStoreCacheMiddleware)

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
            # This cookie carries the OIDC login `state`/`nonce` across the redirect
            # to the provider and back. On an HTTPS deployment it must be Secure (and
            # SameSite=Lax so it still rides the top-level GET back from the provider),
            # matching the app's own session cookie.
            https_only=config.SET_SESSION_COOKIE_SECURE,
            same_site="lax",
        )

    def _mount_routers(self):
        mount_fast_api_routers(self.app)
