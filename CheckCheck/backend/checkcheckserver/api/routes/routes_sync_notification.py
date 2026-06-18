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

# Postgres only: connected SSE clients, fed by a single shared LISTEN
# connection (see _pg_listener_supervisor). Each entry is (queue, user, request).
_pg_clients: List[Tuple[asyncio.Queue, User, Request]] = []
# The single shared asyncpg connection holding LISTEN state, and an event the
# termination callback sets so the supervisor knows to reconnect.
_pg_listen_conn = None
_pg_connection_lost: asyncio.Event | None = None


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

def _sse_from_payload(data: dict) -> str:
    """Render a pg_notify payload into a single SSE message string."""
    noti = SyncNotification(
        timestamp=data["timestamp"],
        cl_id=uuid.UUID(data["cl_id"]),
        cli_id=uuid.UUID(data["cli_id"]) if data.get("cli_id") else None,
        upd_prop=data["upd_prop"],
    )
    return f"data: {noti.model_dump_json()}\n\n"


def _pg_on_notify(conn, pid, channel, payload: str):
    """
    Called on the single shared LISTEN connection for every NOTIFY. Parses the
    payload once and fans it out to all connected clients in the target set.
    Synchronous + put_nowait: queues are unbounded so this never blocks.
    """
    try:
        data = json.loads(payload)
    except (ValueError, KeyError):
        log.warning("[sync] dropping malformed NOTIFY payload")
        return
    targets = data.get("target_user_ids", [])
    sse = _sse_from_payload(data)
    for queue, user, _ in list(_pg_clients):
        if str(user.id) in targets:
            queue.put_nowait(sse)


def _pg_on_terminate(conn):
    if _pg_connection_lost is not None:
        _pg_connection_lost.set()


async def _pg_listener_supervisor():
    """
    Background task (Postgres only). Holds ONE shared asyncpg LISTEN connection
    for the whole process and fans NOTIFYs out to per-client queues. This
    decouples the number of Postgres connections from the number of connected
    SSE clients (one raw connection per client would otherwise exhaust
    max_connections well before any CPU/memory limit).

    If the shared connection drops, all client SSE streams are closed so the
    browsers reconnect — which triggers the frontend resync and recovers any
    events missed during the gap — and the supervisor reconnects.
    """
    import asyncpg

    global _pg_listen_conn
    while True:
        try:
            _pg_listen_conn = await asyncpg.connect(config.POSTGRES_DSN)
            _pg_connection_lost.clear()
            _pg_listen_conn.add_termination_listener(_pg_on_terminate)
            await _pg_listen_conn.add_listener("checkcheck_sync", _pg_on_notify)
        except Exception:
            log.exception("[sync] could not establish Postgres LISTEN; retrying")
            await asyncio.sleep(2)
            continue

        # Hold the connection open until it is reported lost.
        await _pg_connection_lost.wait()
        log.warning("[sync] Postgres LISTEN connection lost; reconnecting")

        # Close client streams so browsers reconnect and resync the missed gap.
        for queue, _, _ in list(_pg_clients):
            queue.put_nowait(None)
        try:
            await _pg_listen_conn.close()
        except Exception:
            pass
        _pg_listen_conn = None
        await asyncio.sleep(1)


async def _postgres_stream(request: Request, user: User):
    """
    Each client gets a personal asyncio.Queue fed by the shared LISTEN
    connection (see _pg_listener_supervisor).
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    _pg_clients.append((queue, user, request))
    try:
        while not await request.is_disconnected():
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30)
                if payload is None:  # connection-lost / shutdown signal
                    break
                yield payload
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"  # prevent proxy / load-balancer timeout
    finally:
        _pg_clients[:] = [t for t in _pg_clients if t[0] is not queue]


# ── SQLite path ───────────────────────────────────────────────────────────────
#
# SQLite is a local-dev convenience only; Postgres is the production target.
# This path just has to be functionally correct — do NOT invest in its scaling
# or long-running behaviour (e.g. multi-worker safety, idle-poll cost). The
# in-process client list and single global drain loop are fine for dev and are
# intentionally not hardened.

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
    global _pg_connection_lost, _pg_listen_conn
    drain_task = None
    pg_task = None
    if config.db_backend == DbBackend.SQLITE:
        drain_task = asyncio.create_task(_sqlite_drain())
    else:
        _pg_connection_lost = asyncio.Event()
        pg_task = asyncio.create_task(_pg_listener_supervisor())
    try:
        yield
    finally:
        if drain_task is not None:
            drain_task.cancel()
        if pg_task is not None:
            pg_task.cancel()
        # Signal all connected clients to close.
        for queue, _, _ in _sqlite_clients:
            await queue.put(None)
        _sqlite_clients.clear()
        for queue, _, _ in _pg_clients:
            queue.put_nowait(None)
        _pg_clients.clear()
        if _pg_listen_conn is not None:
            try:
                await _pg_listen_conn.close()
            except Exception:
                pass
            _pg_listen_conn = None


fast_api_sse_sync_router.lifespan_context = lifespan
