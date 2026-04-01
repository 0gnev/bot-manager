from __future__ import annotations

import asyncio

from bridge import idempotency


def test_check_and_mark_detects_duplicates(tmp_path) -> None:
    idempotency._store.clear()
    idempotency._state_path = str(tmp_path / "state")

    first = asyncio.run(idempotency.check_and_mark("key-1", ttl=60))
    second = asyncio.run(idempotency.check_and_mark("key-1", ttl=60))

    assert first is False
    assert second is True


def test_webhook_idempotency_key_is_stable() -> None:
    from bridge.webhook.router import _webhook_idempotency_key

    body = {"event": "BOOKING_CREATED", "uid": "abc", "startTime": "2026-04-01T10:00:00Z"}
    assert _webhook_idempotency_key(body) == _webhook_idempotency_key(dict(body))
