from __future__ import annotations

import asyncio

from bridge import idempotency


def test_check_and_mark_detects_duplicates() -> None:
    idempotency._store.clear()

    async def _run():
        first = await idempotency.check_and_mark("key-1", ttl=60)
        second = await idempotency.check_and_mark("key-1", ttl=60)
        return first, second

    first, second = asyncio.run(_run())

    assert first is False
    assert second is True


def test_webhook_idempotency_key_is_stable() -> None:
    from bridge.webhook.router import _webhook_idempotency_key

    body = {"event": "BOOKING_CREATED", "uid": "abc", "startTime": "2026-04-01T10:00:00Z"}
    assert _webhook_idempotency_key(body) == _webhook_idempotency_key(dict(body))


def test_begin_finish_processing_blocks_inflight_duplicates() -> None:
    idempotency._store.clear()
    idempotency._inflight.clear()

    async def _run() -> tuple[bool, bool, bool]:
        first = await idempotency.begin_processing("key-2")
        second = await idempotency.begin_processing("key-2")
        await idempotency.finish_processing("key-2", ttl=60)
        third = await idempotency.begin_processing("key-2")
        return first, second, third

    first, second, third = asyncio.run(_run())

    assert first is False
    assert second is True
    assert third is True


def test_abandon_processing_releases_inflight_claim() -> None:
    idempotency._store.clear()
    idempotency._inflight.clear()

    async def _run() -> tuple[bool, bool]:
        first = await idempotency.begin_processing("key-3")
        await idempotency.abandon_processing("key-3")
        second = await idempotency.begin_processing("key-3")
        return first, second

    first, second = asyncio.run(_run())

    assert first is False
    assert second is False
