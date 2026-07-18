from typing import Annotated, Sequence, List, Type, Optional
import os
from pathlib import Path
from urllib.parse import urlparse, urljoin
from fastapi import (
    FastAPI,
    APIRouter,
    Request,
    WebSocket,
    WebSocketDisconnect,
    HTTPException,
    status,
)
from fastapi.responses import FileResponse, HTMLResponse, Response
from checkcheckserver.config import Config
import httpx
import asyncio

config = Config()

from checkcheckserver.log import get_logger

log = get_logger()

fast_api_webclient_router: APIRouter = APIRouter()


def _cache_control_for(file_path: str) -> str:
    """Cache-Control policy for a built frontend asset.

    Two tiers keep the PWA from getting "stuck" on an old build while still
    caching aggressively where it is safe:

    * Content-hashed build assets (Nuxt emits them under ``/_nuxt/`` with a hash
      in the filename) never change under a given URL, so they are cached hard
      and never revalidated — a new deploy references new filenames.
    * Everything else — the app shell (``index.html``), the service worker
      (``sw.js`` / ``workbox-*.js``), the web manifest, icons and favicons — is
      served ``no-cache`` so the browser MUST revalidate before use. Without this
      Starlette sends no ``Cache-Control`` and browsers apply heuristic caching,
      serving a stale ``index.html``/``sw.js`` for hours and never discovering a
      new service worker (the classic "I cleared everything to update" problem).
      ``FileResponse`` still emits ``ETag``/``Last-Modified``, so a revalidation
      is a cheap ``304`` when nothing changed.
    """
    normalized = file_path.replace("\\", "/")
    if "/_nuxt/" in normalized:
        return "public, max-age=31536000, immutable"
    return "no-cache"


# We use the compiled static file client
@fast_api_webclient_router.get("/")
async def serve_client_root(req: Request, path_name: Optional[str] = None):
    # SPA Fallback. Let the Nuxt Client router parse URL
    index_file = f"{config.FRONTEND_FILES_DIR}/index.html"
    headers = {
        "content-type": "text/html; charset=UTF-8",
        "cache-control": _cache_control_for(index_file),
    }
    log.debug(
        f"Server Application '{Path(config.FRONTEND_FILES_DIR).absolute()}/index.html' (RespHeaders: {headers} ReqHeaders: {req.headers})"
    )
    return FileResponse(index_file, headers=headers)


# We use the compiled static file client
@fast_api_webclient_router.get("/{path_name:path}")
async def serve_frontend_files(req: Request, path_name: Optional[str] = None):
    headers = {}

    full_path = Path(config.FRONTEND_FILES_DIR, path_name)
    log.debug(
        f"request frontend path '{path_name}'. full_path '{full_path.absolute()}'  (is existing file: {full_path.is_file()})"
    )
    file: str = None
    if not path_name:
        file = os.path.join(config.FRONTEND_FILES_DIR, "index.html")
    if full_path.is_file():
        file = os.path.join(config.FRONTEND_FILES_DIR, path_name)
    if full_path.is_dir():
        file = os.path.join(config.FRONTEND_FILES_DIR, path_name, "index.html")
    if file is not None and Path(file).exists():
        if file.endswith(".css"):
            headers["content-type"] = "text/css; charset=UTF-8"
        elif file.endswith(".js"):
            headers["content-type"] = "application/javascript; charset=UTF-8"
        elif file.endswith(".html"):
            headers["content-type"] = "text/html; charset=UTF-8"
        elif file.endswith(".json"):
            headers["content-type"] = "application/json; charset=UTF-8"
        headers["cache-control"] = _cache_control_for(file)
        log.debug(
            f"Response on path_name:'{path_name}' file:'{file}' (RespHeaders: {headers} ReqHeaders: {req.headers})"
        )
        return FileResponse(file, headers=headers)
    elif path_name and "_nuxt" in path_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{path_name} could not be found.",
        )
    # SPA Fallback. Let the Nuxt Client router parse URL
    index_file = f"{config.FRONTEND_FILES_DIR}/index.html"
    headers["content-type"] = "text/html; charset=UTF-8"
    headers["cache-control"] = _cache_control_for(index_file)
    log.debug(
        f"Response on path_name:'{path_name}' with default index '{config.FRONTEND_FILES_DIR}/index.html' (RespHeaders: {headers} ReqHeaders: {req.headers})"
    )
    return FileResponse(index_file, headers=headers)
