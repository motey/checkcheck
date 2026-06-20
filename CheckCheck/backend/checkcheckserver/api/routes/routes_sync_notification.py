import uuid
import json
import asyncio
import datetime
from contextlib import asynccontextmanager
from typing import List, Optional, Tuple

from fastapi import (
    Depends,
    APIRouter,
    FastAPI,
    Request,
    Query,
    Header,
    HTTPException,
    status,
)
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials

from checkcheckserver.db.user import User
from checkcheckserver.db.user_session import UserSessionCRUD
from checkcheckserver.db.user_auth import UserAuthCRUD
from checkcheckserver.db.user import UserCRUD
from checkcheckserver.api.auth.security import (
    get_current_user_auth,
    api_token_security,
    SESSION_COOKIE_NAME,
)
from checkcheckserver.api.access import AnonymousPrincipal, link_is_resolvable
from checkcheckserver.api.share_password import verify_share_grant
from checkcheckserver.db.checklist_public_share import CheckListPublicShareCRUD
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

# Each connected SSE client carries a *principal* — either a logged-in ``User``
# (matched by ``user.id``) or an ``AnonymousPrincipal`` from a public link
# (matched by ``.token``). Notifications are routed by whichever the target set
# names; see ``_principal_is_target``.

# SQLite only: connected SSE clients. Each entry is (queue, principal, request).
_sqlite_clients: List[Tuple[asyncio.Queue, object, Request]] = []

# Postgres only: connected SSE clients, fed by a single shared LISTEN
# connection (see _pg_listener_supervisor). Each entry is (queue, principal, request).
_pg_clients: List[Tuple[asyncio.Queue, object, Request]] = []
# The single shared asyncpg connection holding LISTEN state, and an event the
# termination callback sets so the supervisor knows to reconnect.
_pg_listen_conn = None
_pg_connection_lost: asyncio.Event | None = None


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


def _principal_is_target(
    principal, target_user_ids: List[str], target_tokens: List[str]
) -> bool:
    """A notification reaches a connected client if its principal is named in the
    target set — by ``user.id`` (logged-in) or by public-share ``token``
    (anonymous). All ids are compared as strings."""
    pid = getattr(principal, "id", None)
    if pid is not None and str(pid) in target_user_ids:
        return True
    ptoken = getattr(principal, "token", None)
    if ptoken is not None and ptoken in target_tokens:
        return True
    return False


async def resolve_sync_principal(
    request: Request,
    token: Optional[str] = Query(
        default=None,
        description="Public-share token for an anonymous (logged-out) subscriber.",
    ),
    share_grant: Optional[str] = Query(
        default=None,
        description="Grant proving the passphrase of a protected public link (see /unlock).",
    ),
    x_share_grant: Optional[str] = Header(default=None),
    user_session_crud: UserSessionCRUD = Depends(UserSessionCRUD.get_crud),
    user_auth_crud: UserAuthCRUD = Depends(UserAuthCRUD.get_crud),
    user_crud: UserCRUD = Depends(UserCRUD.get_crud),
    api_token: Optional[HTTPAuthorizationCredentials] = Depends(api_token_security),
    public_share_crud: CheckListPublicShareCRUD = Depends(
        CheckListPublicShareCRUD.get_crud
    ),
):
    """Authenticate an SSE subscriber as either a logged-in ``User`` or an
    anonymous principal carrying a valid public-share ``token``.

    An *explicit* valid ``?token=`` wins over an ambient session: a logged-in user
    viewing a public link must receive that card's token-scoped stream, not their
    own identity stream (which has no access to a card shared only via the link,
    so they would silently get no updates). Authed clients that pass no token —
    i.e. every existing client — are unaffected. Token subscription is gated by
    the same public-links config switches as the rest of Phase 5.
    """
    if token and config.SHARING_ENABLED and config.SHARING_PUBLIC_LINKS_ENABLED:
        link = await public_share_crud.get_by_token(token)
        # A passphrase-protected link must carry a valid grant to subscribe — the
        # stream is the same capability as the read surface, so it is gated the
        # same way (see /unlock).
        passphrase_ok = link is not None and (
            link.password_hash is None
            or verify_share_grant(
                x_share_grant or share_grant, token, link.password_hash
            )
        )
        if link_is_resolvable(link) and passphrase_ok:
            return AnonymousPrincipal(token=token)
        # An invalid/expired/locked token falls through to normal session auth
        # below, so a logged-in user with a stale token still gets their own stream.

    has_creds = (
        api_token is not None
        or request.cookies.get(SESSION_COOKIE_NAME) is not None
    )
    if has_creds:
        user_auth = await get_current_user_auth(
            request=request,
            user_session_crud=user_session_crud,
            user_auth_crud=user_auth_crud,
            api_token=api_token,
        )
        if user_auth is not None:
            user = await user_crud.get(user_auth.user_id)
            if user is not None:
                return user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
    )


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
    principal=Depends(resolve_sync_principal),
):
    if config.db_backend == DbBackend.POSTGRES:
        return StreamingResponse(
            _postgres_stream(request, principal),
            media_type="text/event-stream",
        )
    return StreamingResponse(
        _sqlite_stream(request, principal),
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
    # target_user_ids / target_tokens are server-side routing details (other
    # users' ids / secret tokens) — never ship them to the client. Excluding them
    # also keeps the SSE payload identical to the SQLite drain path.
    return (
        "data: "
        f"{noti.model_dump_json(exclude={'target_user_ids', 'target_tokens'})}"
        "\n\n"
    )


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
    target_user_ids = data.get("target_user_ids", [])
    target_tokens = data.get("target_tokens", [])
    sse = _sse_from_payload(data)
    for queue, principal, _ in list(_pg_clients):
        if _principal_is_target(principal, target_user_ids, target_tokens):
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


async def _postgres_stream(request: Request, principal):
    """
    Each client gets a personal asyncio.Queue fed by the shared LISTEN
    connection (see _pg_listener_supervisor).
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    _pg_clients.append((queue, principal, request))
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

async def _sqlite_stream(request: Request, principal):
    """
    Each client gets a personal asyncio.Queue. The background drain loop
    (notify_clients) polls the sync_notifications table, resolves target users,
    and pushes serialised events into the matching queues.
    """
    queue: asyncio.Queue[str] = asyncio.Queue()
    _sqlite_clients.append((queue, principal, request))
    try:
        while not await request.is_disconnected():
            try:
                # Bounded wait so a disconnected client is noticed promptly via
                # the loop's is_disconnected() check, instead of the coroutine
                # blocking on queue.get() forever (which keeps the connection
                # "active" and stalls graceful shutdown). Mirrors the Postgres
                # path; no keepalive is emitted since SQLite is dev-only.
                data = await asyncio.wait_for(queue.get(), timeout=5)
            except asyncio.TimeoutError:
                continue
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
        # target_user_ids / target_tokens are server-side routing details (and
        # list other users' ids / secret tokens) — never ship them to the
        # client; this also keeps the SSE payload identical to the Postgres path.
        payload = (
            "data: "
            f"{noti.notification.model_dump_json(exclude={'target_user_ids', 'target_tokens'})}"
            "\n\n"
        )
        target_user_id_strs = [str(uid) for uid in noti.target_user_ids]
        for queue, principal, _ in list(_sqlite_clients):
            if _principal_is_target(
                principal, target_user_id_strs, noti.target_tokens
            ):
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
