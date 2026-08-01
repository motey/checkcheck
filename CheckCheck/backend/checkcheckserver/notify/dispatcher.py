"""The background sender: one in-process loop that drains the outbox.

Started from a router lifespan and cancelled on shutdown, exactly like the SSE
listener in ``api/routes/routes_sync_notification.py``. The shape is
``while True: drain; wait for a nudge or the tick``, so a message queued by a
request goes out immediately (the producer calls :func:`nudge`) while a message
that became due on its own (a retry backoff expiring) is picked up on the next
tick.

No worker process, no broker: the server runs a single uvicorn process, and
delivery is one async SMTP call with a timeout. The trade-off, and why it is
cheap to reverse, is written up as decision 2 in
``docs/plans/EMAIL_NOTIFICATIONS.md``. ``NOTIFY_DISPATCH_IN_PROCESS=false``
already switches this loop off for anyone who wants to drive
:func:`checkcheckserver.notify.outbox.drain_once` from somewhere else.

The loop never raises out of an iteration: a database hiccup or a bug in one
message must not take the sender down for the rest of the process's life.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Awaitable, Callable, Optional

from fastapi import FastAPI

from checkcheckserver.config import Config
from checkcheckserver.db._session import get_async_session_context
from checkcheckserver.log import get_logger
from checkcheckserver.notify.outbox import DrainResult, drain_once, prune_once

log = get_logger()
config = Config()


# Retention is housekeeping, not delivery: running it on every tick would be a
# pointless DELETE a couple of times a minute.
PRUNE_INTERVAL_SECONDS = 3600

_nudge_event: Optional[asyncio.Event] = None
_dispatcher_task: Optional[asyncio.Task] = None


def nudge() -> None:
    """Ask the dispatcher to look at the outbox now instead of at the next tick.

    Safe to call from anywhere, including when the loop is not running (there is
    then simply nothing to wake). Never raises, so a producer cannot fail
    because of it.
    """
    if _nudge_event is not None:
        _nudge_event.set()


def dispatch_enabled(cfg: Optional[Config] = None) -> bool:
    """Whether this process should run the sender at all.

    Off when the operator moved delivery elsewhere, and off when no channel can
    deliver anything: with mail and webhooks both disabled nothing ever reaches
    the outbox, so the loop would only poll an empty table forever.
    """
    cfg = cfg or config
    if not cfg.NOTIFY_DISPATCH_IN_PROCESS:
        return False
    return cfg.EMAIL_ENABLED or cfg.NOTIFY_WEBHOOK_ENABLED


async def dispatch_once(cfg: Optional[Config] = None) -> DrainResult:
    """One drain against a fresh session. The unit the loop repeats."""
    cfg = cfg or config
    async with get_async_session_context() as session:
        return await drain_once(session, config=cfg)


async def _prune(cfg: Config) -> None:
    async with get_async_session_context() as session:
        await prune_once(session, config=cfg)


async def dispatcher_loop(
    cfg: Optional[Config] = None,
    *,
    once: Optional[Callable[[], Awaitable[object]]] = None,
    prune: Optional[Callable[[], Awaitable[object]]] = None,
) -> None:
    """Drain the outbox forever. Cancelled by the lifespan on shutdown.

    *once* and *prune* are injection points for tests, which drive the loop's
    timing without a database behind it.
    """
    global _nudge_event
    cfg = cfg or config
    _nudge_event = asyncio.Event()
    once = once or (lambda: dispatch_once(cfg))
    prune = prune or (lambda: _prune(cfg))
    tick = max(cfg.NOTIFY_DISPATCH_TICK_SECONDS, 1)
    last_prune = 0.0

    log.info("[notify] dispatcher started (tick %ss)", tick)
    try:
        while True:
            # Cleared before the work, not after: a nudge that arrives *while* a
            # drain is running then survives it and starts the next one straight
            # away, instead of being swallowed and waiting out a whole tick.
            _nudge_event.clear()
            try:
                await once()
                if time.monotonic() - last_prune >= PRUNE_INTERVAL_SECONDS:
                    await prune()
                    last_prune = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                # Keep the loop alive: the next tick may well succeed, and a
                # dead dispatcher means mail silently stops going out.
                log.exception("[notify] dispatcher iteration failed")

            try:
                await asyncio.wait_for(_nudge_event.wait(), timeout=tick)
            except asyncio.TimeoutError:
                pass
    finally:
        _nudge_event = None
        log.info("[notify] dispatcher stopped")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the dispatcher with the app and cancel it on shutdown.

    Attached to a router's ``lifespan_context`` (see
    ``api/routes/routes_notification_settings.py``); FastAPI merges router
    lifespans into the app's.
    """
    global _dispatcher_task
    if dispatch_enabled():
        _dispatcher_task = asyncio.create_task(dispatcher_loop())
    else:
        log.debug("[notify] dispatcher not started (no channel enabled in this process)")
    try:
        yield
    finally:
        if _dispatcher_task is not None:
            _dispatcher_task.cancel()
            try:
                await _dispatcher_task
            except asyncio.CancelledError:
                pass
            except Exception:
                # Shutdown is not the moment to fail over a background task.
                log.exception("[notify] dispatcher ended with an error")
            _dispatcher_task = None
