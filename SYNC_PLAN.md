# Real-time Sync: PostgreSQL LISTEN/NOTIFY Implementation Plan

## Goal

Replace the current proof-of-concept SQLite polling loop with a proper pub/sub
mechanism. Postgres (production) uses native `LISTEN`/`NOTIFY`. SQLite (dev)
keeps the existing drain loop. The frontend is unchanged.

---

## Why the Current Design Breaks for Multi-User

| Problem | Impact |
|---|---|
| One notification row processed per second | Real-time lag grows linearly with backlog |
| No deduplication at insert | N drags on a shared checklist = N full item re-fetches for every connected user |
| `notify_clients` is a global sequential drain | Cannot scale horizontally; one slow delivery blocks everyone |
| SQLite write lock contention | Concurrent users serialise on every notification insert |

The frontend debounce added in the meantime (`useSync.ts`) is a mitigation, not a fix.

---

## Target Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Any API write (item move, create, delete, checklist edit…) │
│                                                             │
│   sync_crud.create(SyncNotification(...))                   │
│         │                                                   │
│         ├─ [SQLite]  INSERT into sync_notifications table   │
│         └─ [Postgres] NOTIFY checkcheck_sync, json_payload  │
└─────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴─────────────────┐
              │  [Postgres]                      │  [SQLite]
              │  SSE endpoint holds a            │  notify_clients loop
              │  dedicated LISTEN connection     │  polls table every 1s
              │  per connected client            │  (unchanged)
              │  → zero latency, no backlog      │
              └──────────────────────────────────┘
                              │
                   All connected SSE clients
                   receive events and filter
                   by user_id (unchanged frontend)
```

---

## Backend Changes

### 1. Detect DB backend at startup

Add a helper in `checkcheckserver/config.py` (or a new `db/_backend.py`):

```python
from enum import Enum

class DbBackend(Enum):
    POSTGRES = "postgres"
    SQLITE = "sqlite"

def get_db_backend(url: str) -> DbBackend:
    if url.startswith("postgresql") or url.startswith("asyncpg"):
        return DbBackend.POSTGRES
    return DbBackend.SQLITE
```

Expose as `config.db_backend`.

---

### 2. Notification creation — add NOTIFY for Postgres

**File:** `checkcheckserver/db/sync_notification.py`

Extend `SyncNotifiationCRUD.create()` to issue a Postgres `NOTIFY` alongside (or
instead of) the table insert:

```python
async def create(self, noti: SyncNotification):
    if config.db_backend == DbBackend.POSTGRES:
        # Resolve target users at write time (owner + collaborators)
        target_ids = await self._resolve_target_user_ids(noti.cl_id)
        payload = json.dumps({
            "timestamp": noti.timestamp,
            "cl_id":     str(noti.cl_id),
            "cli_id":    str(noti.cli_id) if noti.cli_id else None,
            "upd_prop":  noti.upd_prop,
            "target_user_ids": [str(uid) for uid in target_ids],
        })
        await self.session.execute(
            text("SELECT pg_notify('checkcheck_sync', :payload)"),
            {"payload": payload},
        )
        await self.session.commit()
        # Optionally still insert for audit log — or skip the table entirely
    else:
        # SQLite: existing behaviour unchanged
        self.session.add(noti)
        await self.session.commit()
```

`_resolve_target_user_ids` is extracted from the existing
`fetch_next_notificaton` logic (owner + collaborator query) — it already exists,
just needs pulling into a shared private method.

> **Note on deduplication:** For the SQLite path, add an `ON CONFLICT DO UPDATE`
> upsert keyed on `(cl_id, upd_prop)` as a quick improvement while the old drain
> loop is still in use.

---

### 3. SSE endpoint — LISTEN connection for Postgres

**File:** `checkcheckserver/api/routes/routes_sync_notification.py`

Replace / augment `sync_via_server_send_events`:

```python
@fast_api_sse_sync_router.get("/sync", ...)
async def sync_via_server_send_events(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    if config.db_backend == DbBackend.POSTGRES:
        return StreamingResponse(
            _postgres_stream(request, current_user),
            media_type="text/event-stream",
        )
    else:
        # SQLite: existing queue-based approach unchanged
        return StreamingResponse(
            _sqlite_stream(request, current_user),
            media_type="text/event-stream",
        )
```

**Postgres stream:**

```python
async def _postgres_stream(request: Request, user: User):
    import asyncpg

    # LISTEN requires a dedicated connection outside SQLAlchemy's pool
    raw_conn = await asyncpg.connect(config.POSTGRES_DSN)
    queue: asyncio.Queue[str] = asyncio.Queue()

    async def on_notify(conn, pid, channel, payload: str):
        data = json.loads(payload)
        if str(user.id) in data.get("target_user_ids", []):
            await queue.put(payload)

    await raw_conn.add_listener("checkcheck_sync", on_notify)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {payload}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"  # prevent proxy timeouts
    finally:
        await raw_conn.remove_listener("checkcheck_sync", on_notify)
        await raw_conn.close()
```

**Key points:**
- `asyncpg.connect()` is a raw connection, separate from SQLAlchemy's pool —
  this is required; connection pool connections cannot hold `LISTEN` state
- `wait_for(..., timeout=30)` sends a keepalive comment every 30 s to prevent
  proxies / load balancers from closing idle SSE connections
- Cleanup in `finally` is critical — leaked LISTEN connections accumulate

**SQLite stream (`_sqlite_stream`):**  
Move the existing `notification_stream()` logic here verbatim. No changes.

---

### 4. Remove / guard `notify_clients` background task

`notify_clients` and the `lifespan` that starts it should only run in SQLite
mode:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    if config.db_backend == DbBackend.SQLITE:
        broadcast_task = asyncio.create_task(notify_clients())
    yield
    if config.db_backend == DbBackend.SQLITE:
        broadcast_task.cancel()
```

The global `clients` list and the queue-based fan-out become SQLite-only.

---

### 5. Config — expose `POSTGRES_DSN`

`asyncpg.connect()` needs a plain `postgresql://` DSN (not the `postgresql+asyncpg://`
SQLAlchemy form). Derive it from the existing `SQL_DATABASE_URL`:

```python
@property
def POSTGRES_DSN(self) -> str:
    # Strip SQLAlchemy dialect prefix if present
    return self.SQL_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
```

---

## Frontend Changes

**None required.** The SSE event payload shape stays identical. The frontend
debounce in `useSync.ts` remains as a sensible guard against edge cases (e.g.
rapid successive moves by another user).

---

## SQLite Dev-Mode Improvements (do alongside)

These are worth doing now to make dev less painful while production gets Postgres:

1. **Deduplication upsert** — replace the plain `INSERT` with:
   ```sql
   INSERT INTO sync_notifications (cl_id, cli_id, upd_prop, timestamp)
   VALUES (...)
   ON CONFLICT (cl_id, upd_prop) DO UPDATE SET
     cli_id    = excluded.cli_id,
     timestamp = excluded.timestamp
   ```
   Requires adding a unique constraint on `(cl_id, upd_prop)` in a migration.

2. **Drain in bursts** — change `notify_clients` to loop without sleep while
   there are pending rows, and only sleep when the queue is empty:
   ```python
   async def notify_clients():
       while True:
           noti = await notification_crud.fetch_next_notificaton()
           if noti is None:
               await asyncio.sleep(1)
               continue
           # send immediately, no sleep between notifications
   ```

---

## Migration / Deployment Notes

- No schema changes needed for Postgres mode (the `sync_notifications` table
  can remain as an optional audit log or be dropped)
- For SQLite deduplication, one Alembic migration is needed (unique constraint)
- `asyncpg` is likely already a transitive dependency via SQLAlchemy; confirm
  it is in `pyproject.toml` as an explicit dependency
- No changes to `docker-compose` or environment variables beyond the existing
  `SQL_DATABASE_URL`

---

## Rough Effort Estimate

| Task | Effort |
|---|---|
| DB backend detection helper | 30 min |
| `SyncNotifiationCRUD.create()` — add NOTIFY path | 1–2 h |
| SSE endpoint — Postgres LISTEN stream | 2–3 h |
| Guard `notify_clients` / lifespan | 30 min |
| SQLite dedup upsert + migration | 1 h |
| SQLite burst drain | 30 min |
| Integration testing (two browser tabs) | 1–2 h |
| **Total** | **~1.5 days** |

---

## When to Do It

Implement before shipping the **Sharing & Collaboration** features. Multi-user
sync that lags or hammers the DB will be the first thing collaborators notice.
The current SQLite polling + frontend debounce is sufficient for single-user
development in the meantime.
