"""
REST API for tutor actions.

Endpoints:
  POST /api/tutor/reply          — reply to a pending escalation
  GET  /api/tutor/escalations    — list pending escalations
  GET  /api/tutor/escalations/{booking_id} — get escalation details
  POST /api/tutor/chats/{booking_id}/mode  — switch chat mode
  POST /api/tutor/contacts/{contact_id}/mode — switch contact chat mode

Auth: Bearer token (tutor_api_token; falls back to gateway_auth_token).
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from bridge.audit import audit_log
from bridge.bot import registry
from bridge.config import Settings, get_settings
from bridge.db import get_pool
from bridge.delivery import send_student_message
from bridge.knowledge_learning import schedule_capture
from bridge.state import (
    approvals,
    bookings,
    contacts,
    conversations,
    escalations,
    load_controls,
    OperatingMode,
    save_controls,
    save_tutor_time_zone,
)
from bridge.timezones import format_datetime, validate_time_zone_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tutor")


def _reply_target_label(booking_id: str | None, contact: dict | None = None) -> str:
    if booking_id:
        return f"бронь: {booking_id}"
    if contact:
        contact_name = contact.get("name") or contact.get("telegram_username")
        if contact_name:
            return f"контакт: {contact_name}"
        contact_id = contact.get("id")
        if contact_id is not None:
            return f"контакт: {contact_id}"
    return "общий вопрос"


def _serialize_ts(value):
    if value is not None and hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _state_path(settings) -> str | None:
    return getattr(settings, "state_path", None)


async def _load_tutor_time_zone(settings) -> str | None:
    state_path = _state_path(settings)
    if state_path is None:
        return None
    try:
        controls = await load_controls(state_path)
    except Exception:
        logger.warning("Could not load tutor time zone from runtime controls", exc_info=True)
        return None
    return controls.get("tutor_time_zone")


def _display_ts(value, tutor_time_zone: str | None) -> str | None:
    if value is None:
        return None
    return format_datetime(
        value,
        time_zone_name=tutor_time_zone,
        fmt="%d.%m.%Y %H:%M",
        fallback=None,
    )


def _excerpt(text: str | None, limit: int = 160) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


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
    booking_id: str | None
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


class TutorTimeZoneRequest(BaseModel):
    time_zone: str


async def _load_chat_target(
    settings: Settings,
    *,
    booking_id: str | None = None,
    contact_id: int | None = None,
):
    if contact_id is not None:
        contact = await contacts.load(settings.state_path, contact_id)
        if not contact:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")
        return await conversations.load_chat_by_contact(settings.state_path, contact_id), contact

    if not booking_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Either booking_id or contact_id is required",
        )
    return await conversations.load_chat(settings.state_path, booking_id), None


async def _set_chat_mode_impl(
    *,
    settings: Settings,
    mode: OperatingMode,
    booking_id: str | None = None,
    contact_id: int | None = None,
) -> dict:
    chat, _ = await _load_chat_target(
        settings,
        booking_id=booking_id,
        contact_id=contact_id,
    )
    chat.mode = mode
    if mode != OperatingMode.SEMI_AUTO:
        chat.draft = None
    chat.automation_enabled = mode != OperatingMode.MANUAL
    chat.status = "manual_takeover" if mode == OperatingMode.MANUAL else "active"
    chat.current_stage = "mode_changed"
    chat.assigned_human = "tutor" if mode == OperatingMode.MANUAL else None
    await conversations.save_chat(settings.state_path, chat)

    await audit_log(
        "mode",
        "changed",
        booking_id=booking_id,
        actor="tutor",
        detail={
            "new_mode": mode.value,
            "via": "api",
            "contact_id": contact_id,
        },
    )

    return {
        "ok": True,
        "booking_id": booking_id,
        "contact_id": contact_id,
        "mode": mode.value,
    }


async def _set_chat_automation_impl(
    *,
    settings: Settings,
    enabled: bool,
    assigned_human: str | None = "tutor",
    reason: str | None = None,
    booking_id: str | None = None,
    contact_id: int | None = None,
) -> dict:
    chat, _ = await _load_chat_target(
        settings,
        booking_id=booking_id,
        contact_id=contact_id,
    )
    chat.automation_enabled = enabled
    if enabled:
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
        chat.assigned_human = assigned_human or "tutor"
        if reason:
            chat.escalation_reason = reason
    await conversations.save_chat(settings.state_path, chat)

    await audit_log(
        "automation",
        "chat_toggled",
        booking_id=booking_id,
        actor="tutor",
        detail={
            "enabled": enabled,
            "assigned_human": chat.assigned_human,
            "reason": reason,
            "via": "api",
            "contact_id": contact_id,
        },
    )

    return {
        "ok": True,
        "booking_id": booking_id,
        "contact_id": contact_id,
        "automation_enabled": chat.automation_enabled,
        "mode": chat.mode.value,
        "status": chat.status,
    }


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
    contact_id = esc.get("contact_id")

    booking = await bookings.load(settings.state_path, booking_id) if booking_id else None
    if booking and contact_id is None:
        contact_id = booking.get("contact_id")
    contact = await contacts.load(settings.state_path, contact_id) if contact_id is not None else None
    if not booking and not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking/contact not found")

    student_id = (booking or {}).get("telegram_user_id") or (contact or {}).get("telegram_user_id")
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
            detail="Student is not linked to this booking/contact",
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
        contact_id=contact_id,
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
        "Tutor reply sent: booking=%s contact=%s → student=%s",
        booking_id,
        contact_id,
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
        booking_id=booking_id,
        contact_id=contact_id,
        escalation_state="resolved",
        escalation_reason=None,
        current_stage="tutor_reply_sent",
        status="active",
        assigned_human="tutor",
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

    schedule_capture(
        settings=settings,
        source_kind="escalation",
        booking_id=booking_id,
        contact_id=contact_id,
        escalation_id=esc["escalation_id"],
        source_question=esc.get("question"),
        final_answer=body.text,
    )

    # Notify tutor in Telegram that reply was delivered
    owner_bot = registry.get_owner()
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if owner_bot and tutor_chat_id and student_notified:
        try:
            await owner_bot.send_message(
                int(tutor_chat_id),
                f"✅ Ответ отправлен студенту ({_reply_target_label(booking_id, contact)})",
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
    tutor_time_zone = await _load_tutor_time_zone(settings)
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT e.*, b.title AS event_title, b.attendee, c.name AS contact_name, c.telegram_username
        FROM escalations e
        LEFT JOIN bookings b ON b.booking_id = e.booking_id
        LEFT JOIN contacts c ON c.id = e.contact_id
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
        data["created_at_local"] = _display_ts(row["created_at"], tutor_time_zone)
        data["resolved_at_local"] = _display_ts(row["resolved_at"], tutor_time_zone)
        data["display_time_zone"] = tutor_time_zone
        relevant_history = data.get("relevant_history")
        if isinstance(relevant_history, str):
            try:
                data["relevant_history"] = json.loads(relevant_history)
            except (json.JSONDecodeError, TypeError):
                pass
        data["escalation_id"] = data.pop("id")
        attendee = data.pop("attendee", None) or {}
        if isinstance(attendee, str):
            try:
                attendee = json.loads(attendee)
            except (json.JSONDecodeError, TypeError):
                attendee = {}
        data["student_name"] = attendee.get("name", "") or data.pop("contact_name", "") or ""
        data["contact_telegram"] = data.pop("telegram_username", None)
        data["event_title"] = data.get("event_title", "")
        results.append(data)
    return results


@router.get(
    "/chats",
    dependencies=[Depends(_verify_token)],
)
async def list_chat_reviews(
    limit: int = 50,
    settings: Settings = Depends(get_settings),
) -> list[dict]:
    """List tutor-facing conversation summaries without reading raw state files."""
    tutor_time_zone = await _load_tutor_time_zone(settings)
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT
            c.id AS contact_id,
            c.name AS contact_name,
            c.telegram_username,
            c.email,
            c.mode,
            c.status,
            c.automation_enabled,
            c.current_stage,
            c.updated_at,
            c.active_booking_id,
            b.title AS active_booking_title,
            b.start_time AS active_booking_start_time,
            lm.content AS last_message_content,
            lm.created_at AS last_message_at,
            COALESCE(es.pending_count, 0) AS pending_escalations,
            COALESCE(ap.pending_count, 0) AS pending_approvals
        FROM contacts c
        LEFT JOIN bookings b ON b.booking_id = c.active_booking_id
        LEFT JOIN LATERAL (
            SELECT m.content, m.created_at
            FROM messages m
            WHERE m.contact_id = c.id
            ORDER BY m.created_at DESC
            LIMIT 1
        ) lm ON TRUE
        LEFT JOIN (
            SELECT contact_id, COUNT(*) AS pending_count
            FROM escalations
            WHERE status = 'pending' AND contact_id IS NOT NULL
            GROUP BY contact_id
        ) es ON es.contact_id = c.id
        LEFT JOIN (
            SELECT contact_id, COUNT(*) AS pending_count
            FROM approvals
            WHERE status = 'pending' AND contact_id IS NOT NULL
            GROUP BY contact_id
        ) ap ON ap.contact_id = c.id
        WHERE EXISTS (SELECT 1 FROM messages m WHERE m.contact_id = c.id)
           OR EXISTS (SELECT 1 FROM bookings b2 WHERE b2.contact_id = c.id)
        ORDER BY COALESCE(lm.created_at, c.updated_at) DESC, c.id DESC
        LIMIT $1
        """,
        limit,
    )

    return [
        {
            "contact_id": row["contact_id"],
            "contact_name": row["contact_name"],
            "telegram_username": row["telegram_username"],
            "email": row["email"],
            "mode": row["mode"],
            "status": row["status"],
            "automation_enabled": row["automation_enabled"],
            "current_stage": row["current_stage"],
            "updated_at": _serialize_ts(row["updated_at"]),
            "updated_at_local": _display_ts(row["updated_at"], tutor_time_zone),
            "display_time_zone": tutor_time_zone,
            "active_booking": (
                {
                    "booking_id": row["active_booking_id"],
                    "title": row["active_booking_title"],
                    "start_time": _serialize_ts(row["active_booking_start_time"]),
                    "start_time_local": _display_ts(
                        row["active_booking_start_time"],
                        tutor_time_zone,
                    ),
                }
                if row["active_booking_id"]
                else None
            ),
            "last_message_excerpt": _excerpt(row["last_message_content"]),
            "last_message_at": _serialize_ts(row["last_message_at"]),
            "last_message_at_local": _display_ts(row["last_message_at"], tutor_time_zone),
            "pending_escalations": row["pending_escalations"],
            "pending_approvals": row["pending_approvals"],
        }
        for row in rows
    ]


@router.get(
    "/chats/{booking_id}",
    dependencies=[Depends(_verify_token)],
)
async def get_chat_review(
    booking_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return booking-scoped conversation review payload."""
    tutor_time_zone = await _load_tutor_time_zone(settings)
    booking = await bookings.load(settings.state_path, booking_id)
    if not booking:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found")

    contact = None
    contact_id = booking.get("contact_id")
    if contact_id is not None:
        contact = await contacts.load(settings.state_path, contact_id)

    chat = await conversations.load_chat(settings.state_path, booking_id)
    return {
        "booking_id": booking_id,
        "contact_id": contact_id,
        "booking": booking,
        "contact": contact,
        "display_time_zone": tutor_time_zone,
        "chat": chat.to_dict(),
        "messages": chat.messages,
        "pending_escalations": await escalations.list_pending(
            settings.state_path,
            booking_id=booking_id,
        ),
        "pending_approvals": await approvals.list_pending(
            settings.state_path,
            booking_id=booking_id,
        ),
    }


@router.get(
    "/contacts/{contact_id}/chat",
    dependencies=[Depends(_verify_token)],
)
async def get_contact_chat_review(
    contact_id: int,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return contact-scoped conversation review payload."""
    tutor_time_zone = await _load_tutor_time_zone(settings)
    contact = await contacts.load(settings.state_path, contact_id)
    if not contact:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Contact not found")

    active_booking = await bookings.resolve_context_for_contact(
        settings.state_path,
        contact_id,
    )
    chat = await conversations.load_chat_by_contact(settings.state_path, contact_id)
    return {
        "contact_id": contact_id,
        "contact": contact,
        "active_booking": active_booking,
        "related_bookings": await bookings.find_all_by_contact(
            settings.state_path,
            contact_id,
            active_only=False,
        ),
        "display_time_zone": tutor_time_zone,
        "chat": chat.to_dict(),
        "messages": chat.messages,
        "pending_escalations": await escalations.list_pending(
            settings.state_path,
            contact_id=contact_id,
        ),
        "pending_approvals": await approvals.list_pending(
            settings.state_path,
            contact_id=contact_id,
        ),
    }


@router.get(
    "/escalations/{booking_id}",
    dependencies=[Depends(_verify_token)],
)
async def get_escalation(
    booking_id: str,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Get the latest escalation for a booking with booking context."""
    tutor_time_zone = await _load_tutor_time_zone(settings)
    esc = await escalations.load(settings.state_path, booking_id)
    if not esc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escalation not found")

    booking = await bookings.load(settings.state_path, booking_id) if booking_id else None
    if booking:
        attendee = booking.get("attendee") or {}
        esc["student_name"] = attendee.get("name", "")
        esc["event_title"] = booking.get("title", "")
        esc["start_time"] = booking.get("start_time")
        esc["start_time_local"] = _display_ts(booking.get("start_time"), tutor_time_zone)
    elif esc.get("contact_id") is not None:
        contact = await contacts.load(settings.state_path, esc["contact_id"])
        if contact:
            esc["student_name"] = contact.get("name", "")

    esc["display_time_zone"] = tutor_time_zone
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
    return await _set_chat_mode_impl(
        settings=settings,
        mode=body.mode,
        booking_id=booking_id,
    )


@router.post(
    "/contacts/{contact_id}/mode",
    dependencies=[Depends(_verify_token)],
)
async def set_contact_chat_mode(
    contact_id: int,
    body: ModeRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Switch the contact-scoped chat mode without requiring a booking."""
    return await _set_chat_mode_impl(
        settings=settings,
        mode=body.mode,
        contact_id=contact_id,
    )


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
    return await _set_chat_automation_impl(
        settings=settings,
        enabled=body.enabled,
        assigned_human=body.assigned_human,
        reason=body.reason,
        booking_id=booking_id,
    )


@router.post(
    "/contacts/{contact_id}/automation",
    dependencies=[Depends(_verify_token)],
)
async def set_contact_chat_automation(
    contact_id: int,
    body: ChatAutomationRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Explicitly enable or disable automation for a contact-scoped chat."""
    return await _set_chat_automation_impl(
        settings=settings,
        enabled=body.enabled,
        assigned_human=body.assigned_human,
        reason=body.reason,
        contact_id=contact_id,
    )


@router.get(
    "/automation",
    dependencies=[Depends(_verify_token)],
)
async def get_global_automation(
    settings: Settings = Depends(get_settings),
) -> dict:
    """Return current global automation control state."""
    return await load_controls(settings.state_path)


@router.get(
    "/timezone",
    dependencies=[Depends(_verify_token)],
)
async def get_tutor_time_zone(
    settings: Settings = Depends(get_settings),
) -> dict:
    controls = await load_controls(settings.state_path)
    return {
        "time_zone": controls.get("tutor_time_zone"),
        "updated_at": controls.get("updated_at"),
    }


@router.post(
    "/timezone",
    dependencies=[Depends(_verify_token)],
)
async def set_tutor_time_zone_endpoint(
    body: TutorTimeZoneRequest,
    settings: Settings = Depends(get_settings),
) -> dict:
    normalized = validate_time_zone_name(body.time_zone)
    if not normalized:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Invalid IANA time zone",
        )

    controls = await save_tutor_time_zone(
        settings.state_path,
        tutor_time_zone=normalized,
        updated_by="tutor",
        reason="timezone_updated_via_api",
    )
    await audit_log(
        "tutor",
        "timezone_updated",
        actor="tutor",
        detail={"time_zone": normalized, "via": "api"},
    )
    return {
        "ok": True,
        "time_zone": controls.get("tutor_time_zone"),
        "updated_at": controls.get("updated_at"),
    }


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
