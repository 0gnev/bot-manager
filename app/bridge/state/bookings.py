"""
Booking state — persisted in PostgreSQL.

Tables: bookings + contacts (for student identity).
On every save the booking is also exported to Obsidian-compatible markdown.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from bridge.db import get_pool

logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────


def _knowledge_path(state_path: str) -> str:
    """Derive knowledge path from state path (sibling directory)."""
    return str(Path(state_path).parent / "knowledge")


def _coerce_timestamptz(value):
    """Accept ISO strings or datetime-like values for timestamptz columns."""
    if value is None or isinstance(value, (datetime, date)):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    return value


def _row_to_dict(row) -> dict:
    """Convert an asyncpg Record into the dict shape consumers expect."""
    data = dict(row)
    # Remove internal PG fields consumers don't expect
    data.pop("active_booking_id", None)
    data.pop("context_rank", None)
    # Convert timestamps to ISO strings
    for ts_field in ("created_at", "updated_at", "start_time", "end_time"):
        val = data.get(ts_field)
        if val is not None and hasattr(val, "isoformat"):
            data[ts_field] = val.isoformat()
    # Convert JSONB back to dicts/lists
    for jfield in ("organizer", "attendee", "location", "custom_inputs", "draft"):
        val = data.get(jfield)
        if isinstance(val, str):
            try:
                data[jfield] = json.loads(val)
            except (json.JSONDecodeError, TypeError):
                pass
    return data


async def _upsert_contact(conn, data: dict) -> int | None:
    """Upsert a contact from booking data. Returns contact_id or None."""
    attendee = data.get("attendee") or {}
    telegram_user_id = data.get("telegram_user_id")
    telegram_username = (attendee.get("telegram") or "").lstrip("@") or None
    email = attendee.get("email")

    if not telegram_user_id and not telegram_username and not email:
        return None

    # Try to find existing contact by telegram_user_id first, then username, then email.
    contact_id = None
    if telegram_user_id:
        row = await conn.fetchrow(
            "SELECT id FROM contacts WHERE telegram_user_id = $1", telegram_user_id
        )
        if row:
            contact_id = row["id"]

    if contact_id is None and telegram_username:
        row = await conn.fetchrow(
            "SELECT id FROM contacts WHERE lower(telegram_username) = lower($1)",
            telegram_username,
        )
        if row:
            contact_id = row["id"]

    if contact_id is None and email:
        row = await conn.fetchrow(
            "SELECT id FROM contacts WHERE lower(email) = lower($1)",
            email,
        )
        if row:
            contact_id = row["id"]

    name = attendee.get("name")
    phone = attendee.get("phone")
    time_zone = attendee.get("timeZone")

    if contact_id is not None:
        await conn.execute(
            """
            UPDATE contacts
            SET telegram_user_id = COALESCE($2, telegram_user_id),
                telegram_username = COALESCE($3, telegram_username),
                name = COALESCE($4, name),
                email = COALESCE($5, email),
                phone = COALESCE($6, phone),
                time_zone = COALESCE($7, time_zone),
                updated_at = now()
            WHERE id = $1
            """,
            contact_id,
            telegram_user_id,
            telegram_username,
            name,
            email,
            phone,
            time_zone,
        )
        return contact_id

    row = await conn.fetchrow(
        """
        INSERT INTO contacts (telegram_user_id, telegram_username, name, email, phone, time_zone)
        VALUES ($1, $2, $3, $4, $5, $6)
        RETURNING id
        """,
        telegram_user_id,
        telegram_username,
        name,
        email,
        phone,
        time_zone,
    )
    return row["id"]


def _choose_booking_row(rows) -> dict | None:
    if not rows:
        return None
    if len(rows) == 1:
        return _row_to_dict(rows[0])

    active_rows = [row for row in rows if row["context_rank"] == 1]
    if len(active_rows) == 1:
        return _row_to_dict(active_rows[0])

    return None


def _resolve_contact_booking(rows) -> dict | None:
    if not rows:
        return None
    rows = [dict(row) for row in rows]

    explicit = [row for row in rows if row["context_rank"] == 1]
    if explicit:
        return _row_to_dict(explicit[0])

    now = datetime.now(timezone.utc)
    future_rows = []
    for row in rows:
        start_time = row.get("start_time")
        if isinstance(start_time, datetime) and start_time >= now:
            future_rows.append(row)
    if future_rows:
        future_rows.sort(key=lambda item: item.get("start_time") or now)
        return _row_to_dict(future_rows[0])

    rows = sorted(
        rows,
        key=lambda item: (
            item.get("updated_at") or item.get("start_time") or item.get("created_at") or now
        ),
        reverse=True,
    )
    return _row_to_dict(rows[0])


# ── public API ───────────────────────────────────────────────────────────────


async def save(state_path: str, booking_id: str, data: dict) -> None:
    pool = get_pool()
    contact_id = None
    async with pool.acquire() as conn:
        async with conn.transaction():
            contact_id = await _upsert_contact(conn, data)

            await conn.execute(
                """
                INSERT INTO bookings (
                    booking_id, contact_id, event, title, description,
                    start_time, end_time, organizer, attendee, location,
                    meeting_url, custom_inputs, status, telegram_user_id,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6::timestamptz, $7::timestamptz, $8::jsonb, $9::jsonb, $10::jsonb,
                    $11, $12::jsonb, $13, $14,
                    now()
                )
                ON CONFLICT (booking_id) DO UPDATE SET
                    contact_id = COALESCE(EXCLUDED.contact_id, bookings.contact_id),
                    event = COALESCE(EXCLUDED.event, bookings.event),
                    title = COALESCE(EXCLUDED.title, bookings.title),
                    description = COALESCE(EXCLUDED.description, bookings.description),
                    start_time = COALESCE(EXCLUDED.start_time, bookings.start_time),
                    end_time = COALESCE(EXCLUDED.end_time, bookings.end_time),
                    organizer = COALESCE(EXCLUDED.organizer, bookings.organizer),
                    attendee = COALESCE(EXCLUDED.attendee, bookings.attendee),
                    location = COALESCE(EXCLUDED.location, bookings.location),
                    meeting_url = COALESCE(EXCLUDED.meeting_url, bookings.meeting_url),
                    custom_inputs = COALESCE(EXCLUDED.custom_inputs, bookings.custom_inputs),
                    status = COALESCE(EXCLUDED.status, bookings.status),
                    telegram_user_id = COALESCE(EXCLUDED.telegram_user_id, bookings.telegram_user_id),
                    updated_at = now()
                """,
                booking_id,
                contact_id,
                data.get("event"),
                data.get("title"),
                data.get("description"),
                _coerce_timestamptz(data.get("start_time")),
                _coerce_timestamptz(data.get("end_time")),
                json.dumps(data.get("organizer")) if data.get("organizer") else None,
                json.dumps(data.get("attendee")) if data.get("attendee") else None,
                json.dumps(data.get("location")) if data.get("location") else None,
                data.get("meeting_url"),
                json.dumps(data.get("custom_inputs")) if data.get("custom_inputs") is not None else None,
                data.get("status", "active"),
                data.get("telegram_user_id"),
            )

    # Export to Obsidian
    try:
        from obsidian_adapter.writer import export_booking
        export_data = {**data, "booking_id": booking_id, "contact_id": contact_id}
        await export_booking(_knowledge_path(state_path), export_data)
    except Exception as exc:
        logger.warning("Failed to export booking to Obsidian: %s", exc)


async def load(state_path: str, booking_id: str) -> dict | None:
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM bookings WHERE booking_id = $1", booking_id
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def link_telegram_user(
    state_path: str, booking_id: str, telegram_user_id: int
) -> dict | None:
    """Attach telegram_user_id to an existing booking and its contact."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM bookings WHERE booking_id = $1", booking_id
            )
            if row is None:
                return None

            booking_data = _row_to_dict(row)
            booking_data["telegram_user_id"] = telegram_user_id
            contact_id = row["contact_id"] or await _upsert_contact(conn, booking_data)

            # Update booking
            await conn.execute(
                """
                UPDATE bookings
                SET telegram_user_id = $1,
                    contact_id = COALESCE($3, contact_id),
                    updated_at = now()
                WHERE booking_id = $2
                """,
                telegram_user_id,
                booking_id,
                contact_id,
            )

            # Update contact if exists and make this the active booking context.
            if contact_id:
                await conn.execute(
                    """
                    UPDATE contacts
                    SET telegram_user_id = $1,
                        active_booking_id = $2,
                        updated_at = now()
                    WHERE id = $3
                    """,
                    telegram_user_id,
                    booking_id,
                    contact_id,
                )

            updated = await conn.fetchrow(
                "SELECT * FROM bookings WHERE booking_id = $1", booking_id
            )
            return _row_to_dict(updated) if updated else None


async def find_all_by_telegram_user(
    state_path: str, telegram_user_id: int
) -> list[dict]:
    """List active bookings for a Telegram user ordered by explicit context first."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT b.*,
               c.active_booking_id,
               CASE WHEN c.active_booking_id = b.booking_id THEN 1 ELSE 0 END AS context_rank
        FROM bookings b
        LEFT JOIN contacts c ON c.id = b.contact_id
        WHERE b.status = 'active'
          AND (
            b.telegram_user_id = $1
            OR c.telegram_user_id = $1
          )
        ORDER BY context_rank DESC,
                 b.updated_at DESC,
                 b.start_time ASC NULLS LAST,
                 b.created_at DESC
        """,
        telegram_user_id,
    )
    return [_row_to_dict(row) for row in rows]


async def find_by_telegram_user(
    state_path: str, telegram_user_id: int
) -> dict | None:
    """Find the active booking context for a Telegram user when it is unambiguous."""
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT b.*,
               c.active_booking_id,
               CASE WHEN c.active_booking_id = b.booking_id THEN 1 ELSE 0 END AS context_rank
        FROM bookings b
        LEFT JOIN contacts c ON c.id = b.contact_id
        WHERE b.status = 'active'
          AND (
            b.telegram_user_id = $1
            OR c.telegram_user_id = $1
          )
        ORDER BY context_rank DESC,
                 b.updated_at DESC,
                 b.start_time ASC NULLS LAST,
                 b.created_at DESC
        """,
        telegram_user_id,
    )
    return _choose_booking_row(rows)


async def find_all_by_telegram_username(
    state_path: str, username: str
) -> list[dict]:
    """List active, unlinked bookings that match a Telegram username."""
    normalized = username.lower().lstrip("@")
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT b.*,
               c.active_booking_id,
               CASE WHEN c.active_booking_id = b.booking_id THEN 1 ELSE 0 END AS context_rank
        FROM bookings b
        JOIN contacts c ON c.id = b.contact_id
        WHERE b.status = 'active'
          AND b.telegram_user_id IS NULL
          AND lower(c.telegram_username) = $1
        ORDER BY context_rank DESC,
                 b.updated_at DESC,
                 b.start_time ASC NULLS LAST,
                 b.created_at DESC
        """,
        normalized,
    )
    return [_row_to_dict(row) for row in rows]


async def find_by_telegram_username(
    state_path: str, username: str
) -> dict | None:
    """Find an active, unlinked booking where attendee.telegram matches @username."""
    return _choose_booking_row(await get_pool().fetch(
        """
        SELECT b.*,
               c.active_booking_id,
               CASE WHEN c.active_booking_id = b.booking_id THEN 1 ELSE 0 END AS context_rank
        FROM bookings b
        JOIN contacts c ON c.id = b.contact_id
        WHERE b.status = 'active'
          AND b.telegram_user_id IS NULL
          AND lower(c.telegram_username) = lower($1)
        ORDER BY context_rank DESC,
                 b.updated_at DESC,
                 b.start_time ASC NULLS LAST,
                 b.created_at DESC
        """,
        username.lstrip("@"),
    ))


async def find_all_by_contact(
    state_path: str,
    contact_id: int,
    *,
    active_only: bool = True,
) -> list[dict]:
    pool = get_pool()
    conditions = ["b.contact_id = $1"]
    if active_only:
        conditions.append("b.status = 'active'")
    rows = await pool.fetch(
        f"""
        SELECT b.*,
               c.active_booking_id,
               CASE WHEN c.active_booking_id = b.booking_id THEN 1 ELSE 0 END AS context_rank
        FROM bookings b
        LEFT JOIN contacts c ON c.id = b.contact_id
        WHERE {' AND '.join(conditions)}
        ORDER BY context_rank DESC,
                 b.updated_at DESC,
                 b.start_time ASC NULLS LAST,
                 b.created_at DESC
        """,
        contact_id,
    )
    return [_row_to_dict(row) for row in rows]


async def resolve_context_for_contact(
    state_path: str,
    contact_id: int,
) -> dict | None:
    pool = get_pool()
    rows = await pool.fetch(
        """
        SELECT b.*,
               c.active_booking_id,
               CASE WHEN c.active_booking_id = b.booking_id THEN 1 ELSE 0 END AS context_rank
        FROM bookings b
        LEFT JOIN contacts c ON c.id = b.contact_id
        WHERE b.contact_id = $1
          AND b.status = 'active'
        ORDER BY b.updated_at DESC,
                 b.start_time ASC NULLS LAST,
                 b.created_at DESC
        """,
        contact_id,
    )
    return _resolve_contact_booking(rows)
