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
    booking_id: str | None = None
    escalation_id: int | None = None
    text: str


class ReplyResponse(BaseModel):
    ok: bool
    booking_id: str
    escalation_id: int
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

    esc = await _resolve_reply_target(body, settings)
    booking_id = esc["booking_id"]

    booking = await bookings.load(settings.state_path, booking_id)
    if not booking:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")

    student_id = booking.get("telegram_user_id")
    if not student_id:
        await audit_log(
            "escalation",
            "reply_delivery_blocked",
            booking_id=booking_id,
            actor="tutor",
            outcome="failure",
            detail={
                "via": "api",
                "error": "student_not_linked",
                "escalation_id": esc["escalation_id"],
            },
        )
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Student is not linked to this booking",
        )

    student_bot = registry.get_student()
    if student_bot is None:
        await audit_log(
            "escalation",
            "reply_delivery_blocked",
            booking_id=booking_id,
            actor="tutor",
            outcome="failure",
            detail={
                "via": "api",
                "error": "student_bot_unavailable",
                "escalation_id": esc["escalation_id"],
            },
        )
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Student bot is unavailable",
        )

    student_notified = await send_student_message(
        bot=student_bot,
        chat_id=student_id,
        text=body.text,
        booking_id=booking_id,
        settings=settings,
        source="tutor_api_reply",
        actor="tutor",
    )
    if not student_notified:
        await audit_log(
            "escalation",
            "reply_delivery_blocked",
            booking_id=booking_id,
            actor="tutor",
            outcome="failure",
            detail={
                "via": "api",
                "error": "student_delivery_failed",
                "escalation_id": esc["escalation_id"],
            },
        )
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail="Failed to deliver tutor reply to student",
        )

    logger.info(
        "Tutor reply sent: booking=%s → student=%s",
        booking_id,
        student_id,
    )

    resolved = await escalations.resolve_by_id(
        settings.state_path,
        esc["escalation_id"],
        body.text,
        resolved_by="tutor",
    )
    if resolved is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Escalation already resolved")
    await conversations.update_metadata(
        settings.state_path,
        booking_id,
        escalation_state="resolved",
        escalation_reason=None,
        current_stage="tutor_reply_sent",
        status="active",
        assigned_human="tutor",
        automation_enabled=False,
    )
    await audit_log(
        "escalation", "resolved",
        booking_id=booking_id,
        actor="tutor",
        detail={
            "via": "api",
            "student_notified": student_notified,
            "escalation_id": esc["escalation_id"],
        },
    )

    # Notify tutor in Telegram that reply was delivered
    owner_bot = registry.get_owner()
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if owner_bot and tutor_chat_id and student_notified:
        try:
            await owner_bot.send_message(
                int(tutor_chat_id),
                f"✅ Ответ отправлен студенту (бронь: {booking_id})",
            )
        except Exception:
            pass

    return ReplyResponse(
        ok=True,
        booking_id=booking_id,
        escalation_id=esc["escalation_id"],
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
    from bridge.db import get_pool

    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT e.*, b.title AS event_title, b.attendee
        FROM escalations e
        LEFT JOIN bookings b ON b.booking_id = e.booking_id
        WHERE e.status = 'pending'
        ORDER BY e.created_at
        """
    )

    results = []
    for row in rows:
        data = dict(row)
        for ts_field in ("created_at", "resolved_at"):
            val = data.get(ts_field)
            if val is not None and hasattr(val, "isoformat"):
                data[ts_field] = val.isoformat()
        data["escalation_id"] = data.pop("id")
        attendee = data.pop("attendee", None) or {}
        if isinstance(attendee, str):
            import json
            try:
                attendee = json.loads(attendee)
            except (json.JSONDecodeError, TypeError):
                attendee = {}
        data["student_name"] = attendee.get("name", "")
        data["event_title"] = data.get("event_title", "")
        results.append(data)
    return results


@router.get(
    "/escalations/{booking_id}",
    dependencies=[Depends(_verify_token)],
)
async def get_escalation(
    booking_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Get the latest escalation for a booking with booking context."""
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


async def _resolve_reply_target(body: ReplyRequest, settings: Settings) -> dict:
    if body.escalation_id is not None:
        esc = await escalations.load_by_id(settings.state_path, body.escalation_id)
        if not esc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escalation not found")
        if body.booking_id and esc.get("booking_id") != body.booking_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="booking_id does not match escalation_id",
            )
        if esc.get("status") != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Escalation already resolved")
        return esc

    if not body.booking_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either booking_id or escalation_id is required",
        )

    pending = await escalations.list_pending(settings.state_path, booking_id=body.booking_id)
    if not pending:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escalation not found")
    if len(pending) > 1:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Multiple pending escalations for this booking; specify escalation_id",
        )
    return pending[0]


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


# ── Knowledge management ─────────────────────────────────────────────────────


class KnowledgeUpdateRequest(BaseModel):
    file_path: str   # relative path within knowledge dir, e.g. "faq.md"
    content: str     # new markdown content
    action: str = "update"  # create | update | delete


@router.post(
    "/knowledge/update",
    dependencies=[Depends(_verify_token)],
)
async def update_knowledge(
    body: KnowledgeUpdateRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Tutor-only endpoint to create, update, or delete knowledge files.

    Every change is audited in the knowledge_updates table with before/after
    snapshots. The bot NEVER modifies static knowledge files on its own.
    """
    from pathlib import Path

    from bridge.db import get_pool

    knowledge_dir = Path(settings.knowledge_path)
    target = (knowledge_dir / body.file_path).resolve()

    # Prevent path traversal
    if not str(target).startswith(str(knowledge_dir.resolve())):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid file path — must be within knowledge directory",
        )

    # Read current content for audit
    content_before = None
    if target.exists():
        content_before = target.read_text(encoding="utf-8")

    if body.action == "delete":
        if not target.exists():
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="File not found")
        content_after = None
    else:
        content_after = body.content

    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO knowledge_updates (file_path, action, content_before, content_after, requested_by, approved)
        VALUES ($1, $2, $3, $4, 'tutor', TRUE)
        """,
        body.file_path,
        body.action,
        content_before,
        content_after,
    )

    # Apply change to filesystem
    if body.action == "delete":
        target.unlink()
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body.content, encoding="utf-8")

    await audit_log(
        "knowledge",
        body.action,
        actor="tutor",
        detail={"file_path": body.file_path, "via": "api"},
    )

    return {"ok": True, "file_path": body.file_path, "action": body.action}
