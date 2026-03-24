"""
REST API for tutor actions.

Endpoints:
  POST /api/tutor/reply          — reply to a pending escalation
  GET  /api/tutor/escalations    — list pending escalations
  GET  /api/tutor/escalations/{booking_id} — get escalation details

Auth: Bearer token (gateway_auth_token from settings).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel

from bridge.bot import registry
from bridge.config import Settings, get_settings
from bridge.state import bookings, escalations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tutor")


# ── Auth ─────────────────────────────────────────────────────────────────────


def _verify_token(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    expected = f"Bearer {settings.gateway_auth_token}"
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
                await student_bot.send_message(student_id, body.text)
                student_notified = True
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

    await escalations.resolve(settings.state_path, body.booking_id, body.text)

    # Notify tutor in Telegram that reply was delivered
    tutor_bot = registry.get_tutor()
    tutor_chat_id = getattr(settings, "tutor_chat_id", None)
    if tutor_bot and tutor_chat_id and student_notified:
        try:
            await tutor_bot.send_message(
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
