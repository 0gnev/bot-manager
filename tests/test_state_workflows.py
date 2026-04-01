from __future__ import annotations

import asyncio

from bridge.state import approvals, escalations, load_controls, save_controls


def test_approval_state_persists_review_metadata(tmp_path) -> None:
    state_path = str(tmp_path / "state")

    created = asyncio.run(
        approvals.create_approval(
            state_path,
            booking_id="booking-1",
            student_chat_id=123,
            draft_content="Черновик",
            action="answer",
            confidence=0.6,
        )
    )
    resolved = asyncio.run(
        approvals.resolve_approval(
            state_path,
            created["approval_id"],
            "approved",
            reviewer="tutor",
            review_channel="api",
        )
    )

    assert created["status"] == "pending"
    assert resolved["status"] == "approved"
    assert resolved["reviewer"] == "tutor"
    assert resolved["review_channel"] == "api"


def test_escalation_state_persists_reason_and_resolver(tmp_path) -> None:
    state_path = str(tmp_path / "state")

    created = asyncio.run(
        escalations.create(
            state_path,
            "booking-2",
            question="Нужен человек",
            tutor_message_id=555,
            reason="human_review_required",
        )
    )
    resolved = asyncio.run(
        escalations.resolve(
            state_path,
            "booking-2",
            "Отвечаю вручную",
            resolved_by="tutor",
        )
    )

    assert created["reason"] == "human_review_required"
    assert created["status"] == "pending"
    assert resolved["status"] == "resolved"
    assert resolved["resolved_by"] == "tutor"


def test_runtime_controls_persist_global_automation_state(tmp_path) -> None:
    state_path = str(tmp_path / "state")

    initial = asyncio.run(load_controls(state_path))
    updated = asyncio.run(
        save_controls(
            state_path,
            global_automation_enabled=False,
            updated_by="tutor",
            reason="maintenance",
        )
    )
    reloaded = asyncio.run(load_controls(state_path))

    assert initial["global_automation_enabled"] is True
    assert updated["global_automation_enabled"] is False
    assert reloaded["updated_by"] == "tutor"
    assert reloaded["reason"] == "maintenance"
