"""Tests for VAPID keys generated on first boot (chunk K1 of
``docs/plans/PUSH_KEYS_AND_SHARE_SETTING.md``).

The live test server is booted the way an unconfigured instance is: push on (the
default since this chunk) and no ``VAPID_*`` set, so it generates a pair for
itself at startup and stores it in ``instance_secret``. That makes the last test
here an end-to-end check of the whole point of the chunk: a browser talking to a
server nobody configured gets a key it can subscribe with.

The resolution layer itself is driven in-process against the same database, like
the outbox tests, because what it does (read, generate, insert, lose a race) is
not reachable over HTTP.

Two of these tests need the table empty, which it is not: the live server filled
it at boot. They therefore go through the ``clean_instance_secrets`` fixture,
which puts the original rows back afterwards. Without that restore the server's
in-process holder (filled once, at startup) and the database would disagree,
and the public-config test below would be asserting against a key the server no
longer has.
"""

import asyncio
import uuid

import pytest

from utils import req


# ── helpers ───────────────────────────────────────────────────────────────────


def _run(body):
    """Run *body* with a fresh session against the test database."""
    import checkcheckserver.model._tables  # noqa: F401  (registers every table)
    from checkcheckserver.db._engine import db_engine
    from checkcheckserver.db._session import get_async_session_context

    async def _main():
        try:
            async with get_async_session_context() as session:
                return await body(session)
        finally:
            await db_engine.dispose()

    return asyncio.run(_main())


def _run_raw(main):
    """Run a coroutine function that opens its own sessions (the race tests)."""
    from checkcheckserver.db._engine import db_engine

    import checkcheckserver.model._tables  # noqa: F401

    async def _main():
        try:
            return await main()
        finally:
            await db_engine.dispose()

    return asyncio.run(_main())


def _config(**overrides):
    """An instance with push on, and nothing else configured, like the default."""
    from checkcheckserver.config import Config

    settings = {"NOTIFY_PUSH_ENABLED": True}
    settings.update(overrides)
    return Config(**settings)


async def _all_secrets(session):
    from sqlmodel import select

    from checkcheckserver.model.instance_secret import InstanceSecret

    return list((await session.exec(select(InstanceSecret))).all())


@pytest.fixture
def clean_instance_secrets():
    """Empty ``instance_secret`` for one test, then put back what was there.

    The restore is the important half: the running server resolved its keys once,
    at startup, and holds them for the life of the process. A test that leaves a
    different pair in the table would make every later assertion about that
    server's key false.
    """
    from sqlmodel import delete

    from checkcheckserver.model.instance_secret import InstanceSecret

    async def _clear(session):
        rows = await _all_secrets(session)
        saved = [(row.name, row.value) for row in rows]
        await session.exec(delete(InstanceSecret))
        await session.commit()
        return saved

    saved = _run(_clear)

    yield

    async def _restore(session):
        await session.exec(delete(InstanceSecret))
        for name, value in saved:
            session.add(InstanceSecret(name=name, value=value))
        await session.commit()

    _run(_restore)


# ── generation ────────────────────────────────────────────────────────────────


def test_a_fresh_instance_generates_a_usable_pair(clean_instance_secrets):
    """The headline of the chunk: no configuration, and push still has a key."""
    from checkcheckserver.notify import push as push_module
    from checkcheckserver.notify import vapid

    async def body(session):
        keys = await vapid.resolve(session, _config())
        return keys, await _all_secrets(session)

    keys, rows = _run(body)

    assert keys.configured is False
    # The same check `Config` runs over a configured pair: right shape, loadable
    # by py_vapid, and the two halves actually belong together.
    push_module.validate_vapid_keys(
        public_key=keys.public_key, private_key=keys.private_key
    )
    assert {row.name for row in rows} == {
        vapid.PUBLIC_KEY_SECRET_NAME,
        vapid.PRIVATE_KEY_SECRET_NAME,
    }


def test_a_second_boot_reuses_the_stored_pair(clean_instance_secrets):
    """Regenerating on every boot would unsubscribe every device on every restart."""
    from checkcheckserver.notify import vapid

    async def body(session):
        first = await vapid.resolve(session, _config())
        second = await vapid.resolve(session, _config())
        return first, second, await _all_secrets(session)

    first, second, rows = _run(body)

    assert second.public_key == first.public_key
    assert second.private_key == first.private_key
    assert len(rows) == 2  # still one row per half, not four


def test_configured_keys_win_and_nothing_is_generated(clean_instance_secrets):
    """Decision 3: an operator's pair is used as-is and no row is written.

    Also the cheapest regression test for the dev scripts and the E2E harness,
    which both pin their own throwaway pair and must keep working unchanged.
    """
    from checkcheckserver.notify import vapid

    public_key, private_key = vapid.generate_key_pair()

    async def body(session):
        keys = await vapid.resolve(
            session,
            _config(
                VAPID_PUBLIC_KEY=public_key,
                VAPID_PRIVATE_KEY=private_key,
                VAPID_CONTACT_EMAIL="admin@example.test",
            ),
        )
        return keys, await _all_secrets(session)

    keys, rows = _run(body)

    assert keys.configured is True
    assert keys.public_key == public_key
    assert keys.private_key == private_key
    assert keys.subject == "mailto:admin@example.test"
    assert rows == []


# ── the race ──────────────────────────────────────────────────────────────────


def test_concurrent_resolutions_agree_on_one_pair(clean_instance_secrets):
    """Two replicas booting at the same second must not each keep their own key.

    Both find nothing, both generate, both insert; the primary key lets exactly
    one through and the loser has to adopt the winner's pair rather than
    overwrite it. Anything else is an instance where half the pushes are signed
    with a key the browser never subscribed to.
    """
    from checkcheckserver.db._session import get_async_session_context
    from checkcheckserver.notify import vapid

    async def one():
        async with get_async_session_context() as session:
            return await vapid.resolve(session, _config())

    async def main():
        first, second = await asyncio.gather(one(), one())
        async with get_async_session_context() as session:
            return first, second, await _all_secrets(session)

    first, second, rows = _run_raw(main)

    assert first.public_key == second.public_key
    assert first.private_key == second.private_key
    assert len(rows) == 2


def test_the_loser_of_the_insert_race_adopts_the_winners_pair(clean_instance_secrets):
    """The same guarantee, driven deterministically rather than by timing.

    SQLite serialises writes, so the test above can finish without either call
    ever colliding. Here the loser's first read is forced to miss the row that is
    already there, which is exactly what a replica sees a millisecond before its
    own INSERT is rejected: the branch that has to re-read instead of overwriting.
    """
    from checkcheckserver.db import instance_secret as instance_secret_db
    from checkcheckserver.db._session import get_async_session_context
    from checkcheckserver.notify import vapid

    async def main():
        async with get_async_session_context() as session:
            winner = await vapid.resolve(session, _config())

        real_get = instance_secret_db.get
        blinded = {vapid.PRIVATE_KEY_SECRET_NAME, vapid.PUBLIC_KEY_SECRET_NAME}

        async def blind_first_read(session, name):
            if name in blinded:
                blinded.discard(name)
                return None
            return await real_get(session, name)

        instance_secret_db.get = blind_first_read
        try:
            async with get_async_session_context() as session:
                loser = await vapid.resolve(session, _config())
        finally:
            instance_secret_db.get = real_get

        async with get_async_session_context() as session:
            return winner, loser, await _all_secrets(session)

    winner, loser, rows = _run_raw(main)

    assert loser.private_key == winner.private_key
    assert loser.public_key == winner.public_key
    assert len(rows) == 2


# ── the subject claim ─────────────────────────────────────────────────────────


def test_the_jwt_subject_falls_back_when_no_contact_is_configured():
    """Requiring VAPID_CONTACT_EMAIL was part of what made push opt-in.

    RFC 8292 wants a `mailto:` or an `https:` URI, so both fallbacks are things a
    push service accepts, and both are true about the instance rather than
    invented for the claim.
    """
    from checkcheckserver.notify import vapid

    assert (
        vapid.resolve_subject(_config(VAPID_CONTACT_EMAIL="push@example.test"))
        == "mailto:push@example.test"
    )
    assert (
        vapid.resolve_subject(_config(ADMIN_USER_EMAIL="admin@example.test"))
        == "mailto:admin@example.test"
    )
    fallback = _config(ADMIN_USER_EMAIL=None)
    assert vapid.resolve_subject(fallback) == fallback.get_server_url()


# ── the lifespan ──────────────────────────────────────────────────────────────


def test_the_lifespan_fills_the_holder():
    """Where resolution actually happens in a real boot.

    ``Config``'s validators cannot do it (no engine, no event loop) and the two
    read sites must not do it per request, so the dispatcher's lifespan is the
    one place, and it has to run even on an instance that hands delivery to
    something else (``NOTIFY_DISPATCH_IN_PROCESS=false``, which is what this test
    process is).
    """
    from checkcheckserver.notify import dispatcher, vapid

    original_config = dispatcher.config
    dispatcher.config = _config(NOTIFY_DISPATCH_IN_PROCESS=False)
    vapid.reset_keys()

    async def main():
        async with dispatcher.lifespan(app=None):
            return vapid.get_keys()

    try:
        keys = _run_raw(main)
    finally:
        dispatcher.config = original_config
        vapid.reset_keys()

    assert keys is not None
    assert keys.public_key
    # The loop itself stayed down, which is the case that used to leave the
    # browser without a key: an instance draining elsewhere still serves
    # /api/public-config.
    assert dispatcher._dispatcher_task is None


def test_the_holder_stays_empty_when_push_is_switched_off():
    """Nothing to generate and nothing to serve, so no row and no key."""
    from checkcheckserver.notify import vapid

    vapid.reset_keys()
    try:
        assert _run_raw(lambda: vapid.resolve_at_startup(_config(NOTIFY_PUSH_ENABLED=False))) is None
        assert vapid.get_keys() is None
    finally:
        vapid.reset_keys()


# ── the send site ─────────────────────────────────────────────────────────────


@pytest.fixture
def held_keys():
    """Install a pair in the module-level holder for one test, then clear it.

    The holder is what the whole process signs with, so a test that filled it
    and walked away would change what every later test in this session sends.
    """
    from checkcheckserver.notify import vapid

    def install():
        public_key, private_key = vapid.generate_key_pair()
        keys = vapid.VapidKeys(
            public_key=public_key,
            private_key=private_key,
            subject="mailto:generated@example.test",
            configured=False,
        )
        vapid.set_keys(keys)
        return keys

    try:
        yield install
    finally:
        vapid.reset_keys()


def test_a_send_signs_with_the_resolved_pair(held_keys, monkeypatch):
    """The holder is not decoration: it is what reaches ``pywebpush``.

    Before chunk K1 the send site read ``config.VAPID_PRIVATE_KEY`` directly,
    which on a generated instance is None. Signing with the wrong key is not a
    visible failure here, it is a 401 from the push service on every message.
    """
    import pywebpush

    from checkcheckserver.notify import net_guard, push as push_module

    keys = held_keys()
    captured = {}

    def fake_webpush(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    monkeypatch.setattr(
        net_guard, "resolve_addresses_sync", lambda host, port: ["93.184.216.34"]
    )

    push_module._send_sync(
        push_module.OutgoingPush(title="t", body="b", url="https://x/", tag="tag"),
        push_module.PushSubscriptionTarget(
            id=uuid.uuid4(),
            endpoint="https://push.example/signed",
            p256dh="p",
            auth="a",
        ),
        # A config with no VAPID_* at all, which is what an unconfigured
        # instance has: everything the send needs comes from the holder.
        _config(),
    )

    assert captured["vapid_private_key"] == keys.private_key
    assert captured["vapid_claims"] == {"sub": keys.subject}


def test_a_send_without_any_key_refuses_rather_than_unsubscribing(monkeypatch):
    """Startup resolution failed. That is the server's problem, not the device's.

    ``PushEndpointRefused`` rather than ``PushSubscriptionGone``, so the outbox
    row fails for an operator to find and every subscribed device survives: the
    exact classification chunk N3 established, applied to the one new way an
    instance can end up without a key.
    """
    from checkcheckserver.notify import net_guard, push as push_module, vapid

    vapid.reset_keys()
    monkeypatch.setattr(
        net_guard, "resolve_addresses_sync", lambda host, port: ["93.184.216.34"]
    )

    with pytest.raises(push_module.PushEndpointRefused):
        push_module._send_sync(
            push_module.OutgoingPush(title="t", body="b", url="https://x/", tag="t"),
            push_module.PushSubscriptionTarget(
                id=uuid.uuid4(),
                endpoint="https://push.example/keyless",
                p256dh="p",
                auth="a",
            ),
            _config(),
        )


# ── over HTTP ─────────────────────────────────────────────────────────────────


def test_public_config_serves_the_generated_public_key():
    """End to end: a server nobody configured hands a browser a key to subscribe with."""
    from checkcheckserver.notify import push as push_module
    from checkcheckserver.notify import vapid

    public_config = req("api/public-config")
    assert public_config["push_enabled"] is True

    served = public_config["vapid_public_key"]
    assert served

    async def body(session):
        from checkcheckserver.db import instance_secret as instance_secret_db

        return await instance_secret_db.get(session, vapid.PRIVATE_KEY_SECRET_NAME)

    stored_private_key = _run(body)
    assert stored_private_key, "the server should have stored a generated pair at boot"
    # Not just "some base64 string": the key the browser is told to subscribe
    # with has to be the public half of the key this server signs with, or every
    # push is rejected by the push service.
    push_module.validate_vapid_keys(
        public_key=served, private_key=stored_private_key
    )
