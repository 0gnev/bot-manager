"""Sanitize conversation/booking context before it is injected into prompts."""

from __future__ import annotations

from bridge.timezones import format_datetime


def sanitize_booking(booking: dict | None) -> dict | None:
    """Strip internal/sensitive fields before injecting into the AI prompt."""
    if not booking:
        return None
    attendee = booking.get("attendee") or {}
    organizer = booking.get("organizer") or {}
    student_time_zone = attendee.get("timeZone")
    tutor_time_zone = organizer.get("timeZone")
    return {
        "title": booking.get("title", ""),
        "start_time": booking.get("start_time"),
        "end_time": booking.get("end_time"),
        "start_time_local": format_datetime(
            booking.get("start_time"),
            time_zone_name=student_time_zone,
            include_time_zone=bool(student_time_zone),
        ),
        "end_time_local": format_datetime(
            booking.get("end_time"),
            time_zone_name=student_time_zone,
            include_time_zone=bool(student_time_zone),
        ),
        "student_name": attendee.get("name", ""),
        "student_time_zone": student_time_zone,
        "tutor_name": organizer.get("name", ""),
        "tutor_time_zone": tutor_time_zone,
        "meeting_url": booking.get("meeting_url"),
        "status": booking.get("status"),
    }


def sanitize_context(context: dict | None) -> dict:
    if not context:
        return {}
    if "contact" not in context and "booking" not in context and "bookings" not in context:
        return {"booking": sanitize_booking(context)}

    safe_contact = dict(context.get("contact") or {})
    safe_contact.pop("telegram_user_id", None)
    safe_contact.pop("created_at", None)
    safe_contact.pop("updated_at", None)
    safe_bookings = []
    for booking in context.get("bookings") or []:
        sanitized = sanitize_booking(booking)
        if sanitized is not None:
            safe_bookings.append(sanitized)
    return {
        "contact": safe_contact,
        "booking": sanitize_booking(context.get("booking")),
        "bookings": safe_bookings,
        "context_source": context.get("context_source"),
    }
