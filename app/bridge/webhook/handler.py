"""
Business logic for Planerka webhook events.

Saves booking state and notifies linked students on changes.
"""

from __future__ import annotations

import logging
from datetime import datetime

from plannerka_adapter.models import PlanerkaWebhookPayload
from bridge.bot import registry
from bridge.config import Settings
from bridge.state import bookings
from telegram_adapter import templates

logger = logging.getLogger(__name__)

SUPPORTED_EVENTS = {"BOOKING_CREATED", "BOOKING_CANCELLED", "BOOKING_RESCHEDULED"}


async def handle_webhook(body: dict, settings: Settings) -> None:
    try:
        payload = PlanerkaWebhookPayload.model_validate(body)
    except Exception as exc:
        logger.warning("Failed to parse Planerka payload: %s | body=%s", exc, body)
        return

    if payload.event not in SUPPORTED_EVENTS:
        logger.debug("Ignoring unsupported event: %s", payload.event)
        return

    booking_id = payload.get_booking_id()
    if not booking_id:
        logger.warning("Planerka payload missing booking ID: %s", body)
        return

    match payload.event:
        case "BOOKING_CREATED":
            await _on_created(booking_id, payload, settings)
        case "BOOKING_RESCHEDULED":
            await _on_rescheduled(booking_id, payload, settings)
        case "BOOKING_CANCELLED":
            await _on_cancelled(booking_id, payload, settings)


async def _on_created(
    booking_id: str, payload: PlanerkaWebhookPayload, settings: Settings
) -> None:
    attendee = payload.get_attendee()

    data = {
        "booking_id": booking_id,
        "event": payload.event,
        "title": payload.title,
        "description": payload.description,
        "start_time": payload.start_time.isoformat() if payload.start_time else None,
        "end_time": payload.end_time.isoformat() if payload.end_time else None,
        "organizer": payload.organizer.model_dump(by_alias=True, mode="json") if payload.organizer else None,
        "attendee": attendee.model_dump(by_alias=True, mode="json") if attendee else None,
        "location": payload.location.model_dump(by_alias=True, mode="json") if payload.location else None,
        "meeting_url": payload.get_meeting_url(),
        "custom_inputs": (
            [i.model_dump(by_alias=True, mode="json") for i in payload.custom_inputs]
            if payload.custom_inputs else []
        ),
        "telegram_user_id": None,
        "status": "active",
    }

    await bookings.save(settings.state_path, booking_id, data)
    logger.info("Booking created: %s | attendee=%s", booking_id, attendee and attendee.name)


async def _on_rescheduled(
    booking_id: str, payload: PlanerkaWebhookPayload, settings: Settings
) -> None:
    existing = await bookings.load(settings.state_path, booking_id)
    if existing is None:
        await _on_created(booking_id, payload, settings)
        return

    existing["event"] = payload.event
    if payload.start_time:
        existing["start_time"] = payload.start_time.isoformat()
    if payload.end_time:
        existing["end_time"] = payload.end_time.isoformat()
    if payload.get_meeting_url():
        existing["meeting_url"] = payload.get_meeting_url()

    await bookings.save(settings.state_path, booking_id, existing)
    logger.info("Booking rescheduled: %s", booking_id)

    await _notify_student(existing, templates.booking_rescheduled(
        event_title=existing.get("title", "Занятие"),
        start_time=_parse_dt(existing.get("start_time")),
        end_time=_parse_dt(existing.get("end_time")),
        meeting_url=existing.get("meeting_url"),
    ))


async def _on_cancelled(
    booking_id: str, payload: PlanerkaWebhookPayload, settings: Settings
) -> None:
    existing = await bookings.load(settings.state_path, booking_id)
    if existing is None:
        logger.debug("Cancellation for unknown booking: %s", booking_id)
        return

    existing["event"] = payload.event
    existing["status"] = "cancelled"
    await bookings.save(settings.state_path, booking_id, existing)
    logger.info("Booking cancelled: %s", booking_id)

    await _notify_student(existing, templates.booking_cancelled(
        event_title=existing.get("title", "Занятие"),
    ))


async def _notify_student(booking: dict, text: str) -> None:
    """Send a message to the student if they're linked to this booking."""
    telegram_user_id = booking.get("telegram_user_id")
    if not telegram_user_id:
        return

    bot = registry.get_student()
    if bot is None:
        logger.warning("Student bot not available for notification")
        return

    try:
        await bot.send_message(telegram_user_id, text)
        logger.info("Student notified: user_id=%s booking=%s", telegram_user_id, booking.get("booking_id"))
    except Exception as exc:
        logger.error("Failed to notify student %s: %s", telegram_user_id, exc)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
