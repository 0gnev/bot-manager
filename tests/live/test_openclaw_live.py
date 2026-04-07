from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace

import pytest

from bridge.clients.openclaw import OpenclawClient

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_LLM_TESTS") != "1",
        reason="live LLM tests require RUN_LIVE_LLM_TESTS=1",
    ),
]


def _settings() -> SimpleNamespace:
    base_url = os.getenv("OPENCLAW_BASE_URL", "http://127.0.0.1:18789")
    gateway_auth_token = os.getenv("GATEWAY_AUTH_TOKEN")
    if not gateway_auth_token:
        pytest.skip("GATEWAY_AUTH_TOKEN is required for live LLM tests")
    return SimpleNamespace(
        openclaw_base_url=base_url,
        gateway_auth_token=gateway_auth_token,
    )


def test_openclaw_answers_basic_booking_question_without_escalation() -> None:
    client = OpenclawClient(_settings())

    result = asyncio.run(
        client.chat(
            message="Во сколько занятие и где ссылка на встречу?",
            booking_context={
                "contact": {
                    "id": 1,
                    "name": "Иван Петров",
                    "telegram_username": "joji5213",
                },
                "booking": {
                    "booking_id": "live-llm-booking",
                    "title": "Встреча на 30 минут между Ilya Ognev и Иван Петров",
                    "start_time": "2026-04-06T07:00:00+06:00",
                    "end_time": "2026-04-06T07:30:00+06:00",
                    "meeting_url": "https://meet.jit.si/cal/live-llm-booking",
                    "status": "active",
                    "attendee": {
                        "name": "Иван Петров",
                    },
                    "organizer": {
                        "name": "Ilya Ognev",
                    },
                },
                "bookings": [],
                "context_source": "booking",
            },
            history=[],
            knowledge=[],
        )
    )

    assert result["action"] != "escalate"
    assert isinstance(result.get("content"), str)
    assert result["content"].strip()
