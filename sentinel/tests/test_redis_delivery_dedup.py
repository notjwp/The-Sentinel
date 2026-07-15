"""M4: RedisDeliveryDeduper — durable, atomic webhook dedup, proven on fakeredis."""

import asyncio

import fakeredis

from sentinel.api import webhook_controller
from sentinel.api.delivery_dedup import DeliveryDeduper
from sentinel.infrastructure.redis.redis_delivery_dedup import RedisDeliveryDeduper


def _deduper(
    client: fakeredis.FakeAsyncRedis | None = None,
    ttl_seconds: float = 600.0,
) -> RedisDeliveryDeduper:
    return RedisDeliveryDeduper(
        "redis://unused:6379/0",
        ttl_seconds=ttl_seconds,
        client=client if client is not None else fakeredis.FakeAsyncRedis(decode_responses=True),
    )


def test_constructor_does_not_connect():
    RedisDeliveryDeduper("redis://127.0.0.1:1/0")  # lazy from_url; must not raise


def test_first_seen_is_not_duplicate_then_repeat_is():
    async def _run() -> None:
        deduper = _deduper()
        assert await deduper.is_duplicate("guid-1") is False
        assert await deduper.is_duplicate("guid-1") is True
        # Distinct ids are independent.
        assert await deduper.is_duplicate("guid-2") is False

    asyncio.run(_run())


def test_missing_or_unusable_ids_are_never_deduped():
    async def _run() -> None:
        deduper = _deduper()
        for _ in range(2):  # repeats must stay False too
            assert await deduper.is_duplicate(None) is False
            assert await deduper.is_duplicate("") is False
            assert await deduper.is_duplicate("   ") is False
            assert await deduper.is_duplicate(123) is False  # type: ignore[arg-type]

    asyncio.run(_run())


def test_ttl_is_applied_to_the_delivery_key():
    async def _run() -> None:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        deduper = _deduper(client, ttl_seconds=600.0)
        await deduper.is_duplicate("guid-ttl")
        ttl = await client.ttl(f"{RedisDeliveryDeduper.KEY_PREFIX}guid-ttl")
        assert 0 < ttl <= 600  # EX was set, so the entry self-expires

    asyncio.run(_run())


def test_survives_a_restart_because_state_lives_in_redis():
    async def _run() -> None:
        server = fakeredis.FakeServer()  # the shared "real" Redis
        first = _deduper(fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
        assert await first.is_duplicate("guid-x") is False

        # New instance (fresh process after a restart), same Redis.
        second = _deduper(fakeredis.FakeAsyncRedis(server=server, decode_responses=True))
        assert await second.is_duplicate("guid-x") is True

    asyncio.run(_run())


class _ExplodingClient:
    async def set(self, *args, **kwargs):
        raise ConnectionError("redis is down")


def test_fails_open_when_redis_errors():
    async def _run() -> None:
        deduper = RedisDeliveryDeduper("redis://unused", client=_ExplodingClient())
        # Never raises, never dedupes: processing twice beats dropping a webhook.
        assert await deduper.is_duplicate("guid-1") is False
        assert await deduper.is_duplicate("guid-1") is False

    asyncio.run(_run())


def test_build_deduper_selects_backend_by_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6379/0")
    assert isinstance(webhook_controller._build_deduper(), RedisDeliveryDeduper)

    monkeypatch.delenv("REDIS_URL")
    assert isinstance(webhook_controller._build_deduper(), DeliveryDeduper)
