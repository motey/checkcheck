import uuid
import json
import asyncio
from contextlib import asynccontextmanager
from typing import List, Tuple

from fastapi import Depends, APIRouter, FastAPI, Request
from fastapi.responses import StreamingResponse

from checkcheckserver.db.user import User
from checkcheckserver.api.auth.security import get_current_user
from checkcheckserver.log import get_logger
from checkcheckserver.config import Config, DbBackend
from checkcheckserver.db._session import get_async_session_context
from checkcheckserver.db.sync_notification import (
    SyncNotifiationCRUD,
    SyncNotificationPackage,
    SyncNotification,
)

config = Config()
log = get_logger()

fast_api_sse_sync_router: APIRouter = APIRouter()

# SQLite only: connected SSE clients.
# Each entry is (queue, user, request).
_sqlite_clients: List[Tuple[asyncio.Queue, User, Request]] = []


@fast_api_sse_sync_router.get(
    "/sync",
    response_model=SyncNotification,
    response_class=StreamingResponse,
    description="SSE stream that pushes sync notifications to the client.",
    responses={
        200: {
            "model": SyncNotification,
            "description": "Each SSE message is a serialised SyncNotification.",
        }
    },
)
async def sync_via_server_send_events(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    if config.db_backend == DbBackend.POSTGRES:
        return StreamingResponse(
            _postgres_stream(request, current_user),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _sqlite_stream(request, current_user),
        media_type="text/event-stream",
    )


# ── Postgres path ─────────────────────────────────────────────────────────────

async def _postgres_stream(request: Request, user: User):
    """
    Hold a dedicated raw asyncpg LISTEN connection per connected client.
    SQLAlchemy's connection pool cannot hold LISTEN state, so we bypass it.
    """
    import asyncpg

    raw_conn = await asyncpg.connect(config.POSTGRES_DSN)
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def on_notify(conn, pid, channel, payload: str):
        data = json.loads(payload)
        if str(user.id) in data.get("target_user_ids", []):
            await queue.put(payload)

    await raw_conn.add_listener("checkcheck_sync", on_notify)
    try:
        while not await request.is_disconnected():
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30)
                data = json.loads(payload)
                noti = SyncNotification(
                    timestamp=data["timestamp"],
                    cl_id=uuid.UUID(data["cl_id"]),
                    cli_id=uuid.UUID(data["cli_id"]) if data.get("cli_id") else None,
                    upd_prop=data["upd_prop"],
                )
                yield f"data: {noti.model_dump_json()}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"  # prevent proxy / load-balancer timeout
    finally:
        await raw_conn.remove_listener("checkcheck_sync", on_notify)
        await raw_conn.close()


# ── SQLite path ───────────────────────────────────────────────────────────────

async def _sqlite_stream(request: Request, user: User):
    """
    Each client gets a personal asyncio.Queue. The background drain loop
    (notify_clients) polls the sync_notifications table, resolves target users,
    and pushes serialised events into the matching queues.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    _sqlite_clients.append((queue, user, request))
    try:
        while not await request.is_disconnected():
            data = await queue.get()
            if data is None:  # shutdown signal
                break
            yield data
    finally:
        _sqlite_clients[:] = [t for t in _sqlite_clients if t[0] is not queue]


async def _sqlite_drain():
    """
    Background task (SQLite only). Drains the sync_notifications table and
    fans out to connected clients. Runs without sleep while there are pending
    rows; sleeps 1 s only when the queue is empty.
    """
    while True:
        noti: SyncNotificationPackage | None = None
        async with get_async_session_context() as session:
            async with SyncNotifiationCRUD.crud_context(session) as crud:
                noti = await crud.fetch_next_notificaton()

        if noti is None:
            await asyncio.sleep(1)
            continue

        # Fan out to all connected clients that are in the target set.
        payload = f"data: {noti.notification.model_dump_json()}\n\n"
        for queue, user, _ in list(_sqlite_clients):
            if user.id in noti.target_user_ids:
                await queue.put(payload)
        # No sleep — loop immediately while rows are pending.


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    drain_task = None
    if config.db_backend == DbBackend.SQLITE:
        drain_task = asyncio.create_task(_sqlite_drain())
    try:
        yield
    finally:
        if drain_task is not None:
            drain_task.cancel()
        # Signal all connected SQLite clients to close.
        for queue, _, _ in _sqlite_clients:
            await queue.put(None)
        _sqlite_clients.clear()


fast_api_sse_sync_router.lifespan_context = lifespan
