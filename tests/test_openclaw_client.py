from __future__ import annotations

import asyncio
from types import SimpleNamespace

from bridge.clients import openclaw


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"choices": [{"message": {"content": "plain text instead of json"}}]}


class _FakeAsyncClient:
    requests: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        return None

    async def __aenter__(self) -> _FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def post(self, *args, **kwargs) -> _FakeResponse:
        self.__class__.requests.append(kwargs)
        return _FakeResponse()


def test_openclaw_invalid_json_falls_back_to_escalation(monkeypatch) -> None:
    _FakeAsyncClient.requests = []
    settings = SimpleNamespace(
        openclaw_base_url="http://openclaw:18789",
        gateway_auth_token="token",
        openclaw_gateway_model="openclaw",
    )
    audit_events: list[tuple[str, str, dict]] = []

    async def fake_audit_log(event_type: str, action: str, **kwargs) -> None:
        audit_events.append((event_type, action, kwargs))

    monkeypatch.setattr(openclaw.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(openclaw, "audit_log", fake_audit_log)

    client = openclaw.OpenclawClient(settings)
    result = asyncio.run(
        client.chat(
            message="Когда занятие?",
            booking_context={},
            history=[],
            knowledge=[],
        )
    )

    assert result["action"] == "escalate"
    assert result["confidence"] == 0.0
    assert _FakeAsyncClient.requests[0]["json"]["model"] == "openclaw"
    assert any(
        event_type == "ai"
        and action == "response_received"
        and kwargs.get("outcome") == "failure"
        and kwargs.get("detail", {}).get("error") == "invalid_json"
        for event_type, action, kwargs in audit_events
    )
