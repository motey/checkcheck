"""The background sender: one in-process loop that drains the outbox.

Since chunk R2 of the date-reminder plan the same tick also **scans for due
reminders** (``notify/reminders.py``), because a reminder coming due is the one
notification nothing else in the process notices. Both halves are per-tick work;
housekeeping still runs hourly beside them.

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
from checkcheckserver.notify.reminders import reminders_enabled, scan_due_once

log = get_logger()
config = Config()


# Retention is housekeeping, not delivery: running it on every tick would be a
# pointless DELETE a couple of times a minute. Covers both the delivery queue and
# the in-app feed (chunk E6).
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


def channels_enabled(cfg: Optional[Config] = None) -> bool:
    """Whether anything can ever reach the outbox on this instance."""
    cfg = cfg or config
    return bool(cfg.EMAIL_ENABLED or cfg.NOTIFY_WEBHOOK_ENABLED)


def dispatch_enabled(cfg: Optional[Config] = None) -> bool:
    """Whether this process should run the loop at all.

    Off when the operator moved delivery elsewhere. Otherwise on when there is
    anything for the loop to do: something to deliver, something to tidy up
    (feed retention, chunk E6), or reminders to watch for. That last one is why
    an instance with neither mail nor webhooks still runs the loop by default:
    an in-app-only reminder is a perfectly good reminder, and nothing else in
    the process notices that a reminder came due. The drain itself is skipped
    when no channel is enabled (see :func:`dispatch_once`), so the loop never
    polls a table nothing can write to.
    """
    cfg = cfg or config
    if not cfg.NOTIFY_DISPATCH_IN_PROCESS:
        return False
    return (
        channels_enabled(cfg)
        or cfg.NOTIFY_FEED_RETENTION_DAYS > 0
        or reminders_enabled(cfg)
    )


async def dispatch_once(cfg: Optional[Config] = None) -> DrainResult:
    """One scan and one drain against a fresh session. The unit the loop repeats.

    A session is opened whenever there is *either* kind of work, because the
    reminder scan needs one even on an instance that sends nothing at all: it
    reads a table the app writes, and delivers in-app. The drain stays skipped
    without a channel, since nothing could have queued anything.
    """
    cfg = cfg or config
    scan_wanted = reminders_enabled(cfg)
    if not channels_enabled(cfg) and not scan_wanted:
        # Nothing can have queued anything and no reminder can be waiting, so do
        # not open a session to find out.
        return DrainResult()
    async with get_async_session_context() as session:
        if scan_wanted:
            try:
                await scan_due_once(session, config=cfg)
            except Exception:
                # The two halves of a tick are independent, and mail is the older
                # and more critical path: a reminder the scan cannot handle must
                # not stop everything else from going out, tick after tick. The
                # rollback leaves the session usable for the drain below.
                log.exception("[reminders] scan failed")
                await session.rollback()
        if not channels_enabled(cfg):
            return DrainResult()
        return await drain_once(session, config=cfg)


async def _prune(cfg: Config) -> None:
    """The hourly housekeeping pass: the delivery queue, the feed, the reminders.

    Three tables with three retentions, run together because they are the same
    kind of chore and none is worth its own timer. The feed prune is imported
    here rather than at module import time to keep this module free of a cycle
    (``db.notification`` imports the outbox, which is where the other half of
    this pass lives).
    """
    from checkcheckserver.db.notification import prune_feed_once
    from checkcheckserver.db.scheduled_notification import prune_reminders_once

    async with get_async_session_context() as session:
        await prune_once(session, config=cfg)
        await prune_feed_once(session, config=cfg)
        await prune_reminders_once(session)


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
    # None rather than 0.0: `time.monotonic()` counts from an arbitrary point
    # (on Linux, since boot), so "0.0 was an hour ago" is true on a machine that
    # has been up a while and false on one that has just started. Housekeeping
    # ran or did not run depending on the host's uptime, which is not a thing
    # anybody should have to reason about. None means "never yet", so the first
    # pass always tidies up.
    last_prune: Optional[float] = None

    log.info("[notify] dispatcher started (tick %ss)", tick)
    try:
        while True:
            # Cleared before the work, not after: a nudge that arrives *while* a
            # drain is running then survives it and starts the next one straight
            # away, instead of being swallowed and waiting out a whole tick.
            _nudge_event.clear()
            try:
                await once()
                if (
                    last_prune is None
                    or time.monotonic() - last_prune >= PRUNE_INTERVAL_SECONDS
                ):
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
