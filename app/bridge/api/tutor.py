"""
REST API for tutor actions.

Endpoints:
  POST /api/tutor/reply          — reply to a pending escalation
  GET  /api/tutor/escalations    — list pending escalations
  GET  /api/tutor/escalations/{booking_id} — get escalation details
  POST /api/tutor/chats/{booking_id}/mode  — switch chat mode

Auth: Bearer token (tutor_api_token; falls back to gateway_auth_token).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.config import Settings, get_settings
from bridge.delivery import send_student_message
from bridge.state import (
    bookings,
    conversations,
    escalations,
    load_controls,
    OperatingMode,
    save_controls,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tutor")


# ── Auth ─────────────────────────────────────────────────────────────────────


def _verify_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    # Prefer dedicated tutor API token; fall back to gateway token for backwards compat
    expected_token = settings.tutor_api_token or settings.gateway_auth_token
    expected = f"Bearer {expected_token}"
    if not authorization or authorization != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization header",
        )


# ── Models ───────────────────────────────────────────────────────────────────


class ReplyRequest(BaseModel):
    booking_id: str
    text: str


class ReplyResponse(BaseModel):
    ok: bool
    booking_id: str
    student_notified: bool


class ModeRequest(BaseModel):
    mode: OperatingMode


class ChatAutomationRequest(BaseModel):
    enabled: bool
    assigned_human: str | None = "tutor"
    reason: str | None = None


class GlobalAutomationRequest(BaseModel):
    enabled: bool
    reason: str | None = None


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post(
    "/reply",
    response_model=ReplyResponse,
    dependencies=[Depends(_verify_token)],
)
async def tutor_reply(
    body: ReplyRequest,
    settings: Settings = Depends(get_settings),
) -> ReplyResponse:
    """Tutor replies to a pending escalation. Sends the answer to the student."""

    esc = await escalations.load(settings.state_path, body.booking_id)
    if not esc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escalation not found")

    if esc.get("status") != "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Escalation already resolved")

    booking = await bookings.load(settings.state_path, body.booking_id)
    if not booking:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")

    student_id = booking.get("telegram_user_id")
    student_notified = False

    if student_id:
        student_bot = registry.get_student()
        if student_bot:
            try:
                student_notified = await send_student_message(
                    bot=student_bot,
                    chat_id=student_id,
                    text=body.text,
                    booking_id=body.booking_id,
                    settings=settings,
                    source="tutor_api_reply",
                    actor="tutor",
                )
                if student_notified:
                    logger.info(
                        "Tutor reply sent: booking=%s → student=%s",
                        body.booking_id, student_id,
                    )
            except Exception as exc:
                logger.error("Failed to send tutor reply to student %s: %s", student_id, exc)
        else:
            logger.warning("Student bot not available for tutor reply")
    else:
        logger.warning("Student not linked for booking %s", body.booking_id)

    await escalations.resolve(
        settings.state_path,
        body.booking_id,
        body.text,
        resolved_by="tutor",
    )
    await conversations.update_metadata(
        settings.state_path,
        body.booking_id,
        escalation_state="resolved",
        escalation_reason=None,
        current_stage="tutor_reply_sent",
        status="active",
        assigned_human="tutor",
        automation_enabled=False,
    )
    await audit_log(
        "escalation", "resolved",
        booking_id=body.booking_id,
        actor="tutor",
        detail={"via": "api", "student_notified": student_notified},
    )

    # Notify tutor in Telegram that reply was delivered
    owner_bot = registry.get_owner()
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if owner_bot and tutor_chat_id and student_notified:
        try:
            await owner_bot.send_message(
                int(tutor_chat_id),
                f"✅ Ответ отправлен студенту (бронь: {body.booking_id})",
            )
        except Exception:
            pass

    return ReplyResponse(
        ok=True,
        booking_id=body.booking_id,
        student_notified=student_notified,
    )


@router.get(
    "/escalations",
    dependencies=[Depends(_verify_token)],
)
async def list_escalations(
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """List all pending escalations with booking context."""
    import json
    from pathlib import Path

    esc_dir = Path(settings.state_path) / "escalations"
    if not esc_dir.exists():
        return []

    results = []
    for path in sorted(esc_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("status") != "pending":
                continue
            booking_id = data.get("booking_id", "")
            booking = await bookings.load(settings.state_path, booking_id)
            attendee = (booking or {}).get("attendee") or {}
            data["student_name"] = attendee.get("name", "")
            data["event_title"] = (booking or {}).get("title", "")
            results.append(data)
        except Exception:
            continue
    return results


@router.get(
    "/escalations/{booking_id}",
    dependencies=[Depends(_verify_token)],
)
async def get_escalation(
    booking_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Get a specific escalation with full booking context."""
    esc = await escalations.load(settings.state_path, booking_id)
    if not esc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escalation not found")

    booking = await bookings.load(settings.state_path, booking_id)
    if booking:
        attendee = booking.get("attendee") or {}
        esc["student_name"] = attendee.get("name", "")
        esc["event_title"] = booking.get("title", "")
        esc["start_time"] = booking.get("start_time")

    return esc


@router.post(
    "/chats/{booking_id}/mode",
    dependencies=[Depends(_verify_token)],
)
async def set_chat_mode(
    booking_id: str,
    body: ModeRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Switch the chat's operating mode through the REST control path."""
    chat = await conversations.load_chat(settings.state_path, booking_id)
    chat.mode = body.mode
    if body.mode != OperatingMode.SEMI_AUTO:
        chat.draft = None
    chat.automation_enabled = body.mode != OperatingMode.MANUAL
    chat.status = "manual_takeover" if body.mode == OperatingMode.MANUAL else "active"
    chat.current_stage = "mode_changed"
    chat.assigned_human = "tutor" if body.mode == OperatingMode.MANUAL else None
    await conversations.save_chat(settings.state_path, chat)

    await audit_log(
        "mode",
        "changed",
        booking_id=booking_id,
        actor="tutor",
        detail={"new_mode": body.mode.value, "via": "api"},
    )

    return {"ok": True, "booking_id": booking_id, "mode": body.mode.value}


@router.post(
    "/chats/{booking_id}/automation",
    dependencies=[Depends(_verify_token)],
)
async def set_chat_automation(
    booking_id: str,
    body: ChatAutomationRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Explicitly enable or disable automation for a single chat."""
    chat = await conversations.load_chat(settings.state_path, booking_id)
    chat.automation_enabled = body.enabled
    if body.enabled:
        chat.status = "active"
        chat.current_stage = "automation_resumed"
        if chat.mode == OperatingMode.MANUAL:
            chat.mode = OperatingMode.SEMI_AUTO
        chat.assigned_human = None
        chat.escalation_reason = None
    else:
        chat.status = "manual_takeover"
        chat.current_stage = "manual_takeover"
        chat.mode = OperatingMode.MANUAL
        chat.assigned_human = body.assigned_human or "tutor"
        if body.reason:
            chat.escalation_reason = body.reason
    await conversations.save_chat(settings.state_path, chat)

    await audit_log(
        "automation",
        "chat_toggled",
        booking_id=booking_id,
        actor="tutor",
        detail={
            "enabled": body.enabled,
            "assigned_human": chat.assigned_human,
            "reason": body.reason,
            "via": "api",
        },
    )

    return {
        "ok": True,
        "booking_id": booking_id,
        "automation_enabled": chat.automation_enabled,
        "mode": chat.mode.value,
        "status": chat.status,
    }


@router.get(
    "/automation",
    dependencies=[Depends(_verify_token)],
)
async def get_global_automation(
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return current global automation control state."""
    return await load_controls(settings.state_path)


@router.post(
    "/automation",
    dependencies=[Depends(_verify_token)],
)
async def set_global_automation(
    body: GlobalAutomationRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Enable or disable automation globally."""
    controls = await save_controls(
        settings.state_path,
        global_automation_enabled=body.enabled,
        updated_by="tutor",
        reason=body.reason,
    )

    await audit_log(
        "automation",
        "global_toggled",
        actor="tutor",
        detail={
            "enabled": body.enabled,
            "reason": body.reason,
            "via": "api",
        },
    )

    return {"ok": True, **controls}
