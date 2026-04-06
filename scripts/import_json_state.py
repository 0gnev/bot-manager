"""
One-shot import of existing JSON state files into PostgreSQL.

Usage:
    python -m scripts.import_json_state [--state-path /workspace/data/state] [--database-url postgresql://...]

Reads:
  {state_path}/bookings/*.json
  {state_path}/conversations/*.json
  {state_path}/escalations/*.json
  {state_path}/approvals/*.json
  {state_path}/runtime_controls.json
  {state_path}/idempotency.json

Idempotent: uses ON CONFLICT to skip already-imported records.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


async def import_bookings(conn: asyncpg.Connection, state_path: Path) -> int:
    bookings_dir = state_path / "bookings"
    if not bookings_dir.exists():
        return 0

    count = 0
    for path in sorted(bookings_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue

        booking_id = data.get("booking_id", path.stem)
        attendee = data.get("attendee") or {}
        telegram_user_id = data.get("telegram_user_id")
        telegram_username = (attendee.get("telegram") or "").lstrip("@") or None

        # Upsert contact
        contact_id = None
        if telegram_user_id or telegram_username or attendee.get("email"):
            row = await conn.fetchrow(
                """
                INSERT INTO contacts (telegram_user_id, telegram_username, name, email, phone, time_zone)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (telegram_user_id) DO UPDATE SET
                    telegram_username = COALESCE(EXCLUDED.telegram_username, contacts.telegram_username),
                    name = COALESCE(EXCLUDED.name, contacts.name),
                    email = COALESCE(EXCLUDED.email, contacts.email),
                    phone = COALESCE(EXCLUDED.phone, contacts.phone),
                    time_zone = COALESCE(EXCLUDED.time_zone, contacts.time_zone),
                    updated_at = now()
                RETURNING id
                """,
                telegram_user_id,
                telegram_username,
                attendee.get("name"),
                attendee.get("email"),
                attendee.get("phone"),
                attendee.get("timeZone"),
            )
            contact_id = row["id"]

        await conn.execute(
            """
            INSERT INTO bookings (
                booking_id, contact_id, event, title, description,
                start_time, end_time, organizer, attendee, location,
                meeting_url, custom_inputs, status, telegram_user_id,
                created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8::jsonb, $9::jsonb, $10::jsonb,
                $11, $12::jsonb, $13, $14,
                $15, $16
            )
            ON CONFLICT (booking_id) DO NOTHING
            """,
            booking_id,
            contact_id,
            data.get("event"),
            data.get("title"),
            data.get("description"),
            _parse_dt(data.get("start_time")),
            _parse_dt(data.get("end_time")),
            json.dumps(data.get("organizer")) if data.get("organizer") else None,
            json.dumps(attendee) if attendee else None,
            json.dumps(data.get("location")) if data.get("location") else None,
            data.get("meeting_url"),
            json.dumps(data.get("custom_inputs")) if data.get("custom_inputs") is not None else None,
            data.get("status", "active"),
            telegram_user_id,
            _parse_dt(data.get("created_at")) or datetime.now(timezone.utc),
            _parse_dt(data.get("updated_at")) or datetime.now(timezone.utc),
        )
        count += 1

    logger.info("Imported %d bookings", count)
    return count


async def import_conversations(conn: asyncpg.Connection, state_path: Path) -> int:
    conv_dir = state_path / "conversations"
    if not conv_dir.exists():
        return 0

    count = 0
    for path in sorted(conv_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue

        booking_id = path.stem

        # Check that booking exists
        exists = await conn.fetchval(
            "SELECT 1 FROM bookings WHERE booking_id = $1", booking_id
        )
        if not exists:
            logger.warning("Skipping conversation %s: booking not found", booking_id)
            continue

        # Handle legacy format (plain list) vs new format (dict with metadata)
        if isinstance(data, list):
            messages = data
            meta = {}
        else:
            messages = data.get("messages", [])
            meta = data

        # Update chat metadata on booking
        if meta:
            await conn.execute(
                """
                UPDATE bookings SET
                    mode = $2,
                    status = $3,
                    automation_enabled = $4,
                    assigned_human = $5,
                    escalation_state = $6,
                    scenario_type = $7,
                    current_stage = $8,
                    confidence = $9,
                    escalation_reason = $10,
                    draft = $11::jsonb
                WHERE booking_id = $1
                """,
                booking_id,
                meta.get("mode", "auto"),
                meta.get("status", "active"),
                meta.get("automation_enabled", True),
                meta.get("assigned_human"),
                meta.get("escalation_state", "none"),
                meta.get("scenario_type", "general_support"),
                meta.get("current_stage", "new"),
                meta.get("confidence"),
                meta.get("escalation_reason"),
                json.dumps(meta.get("draft")) if meta.get("draft") else None,
            )

        # Insert messages
        for msg in messages:
            ts = _parse_dt(msg.get("ts")) or datetime.now(timezone.utc)
            await conn.execute(
                "INSERT INTO messages (booking_id, role, content, created_at) VALUES ($1, $2, $3, $4)",
                booking_id,
                msg.get("role", "user"),
                msg.get("content", ""),
                ts,
            )
        count += 1

    logger.info("Imported conversations for %d bookings", count)
    return count


async def import_escalations(conn: asyncpg.Connection, state_path: Path) -> int:
    esc_dir = state_path / "escalations"
    if not esc_dir.exists():
        return 0

    count = 0
    for path in sorted(esc_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue

        booking_id = data.get("booking_id", path.stem)
        exists = await conn.fetchval(
            "SELECT 1 FROM bookings WHERE booking_id = $1", booking_id
        )
        if not exists:
            logger.warning("Skipping escalation %s: booking not found", booking_id)
            continue

        await conn.execute(
            """
            INSERT INTO escalations (
                booking_id, status, reason, question, tutor_message_id,
                tutor_reply, resolved_by, created_at, resolved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            """,
            booking_id,
            data.get("status", "pending"),
            data.get("reason"),
            data.get("question", ""),
            data.get("tutor_message_id"),
            data.get("tutor_reply"),
            data.get("resolved_by"),
            _parse_dt(data.get("created_at")) or datetime.now(timezone.utc),
            _parse_dt(data.get("resolved_at")),
        )
        count += 1

    logger.info("Imported %d escalations", count)
    return count


async def import_approvals(conn: asyncpg.Connection, state_path: Path) -> int:
    appr_dir = state_path / "approvals"
    if not appr_dir.exists():
        return 0

    count = 0
    for path in sorted(appr_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
            continue

        booking_id = data.get("booking_id")
        if not booking_id:
            continue

        exists = await conn.fetchval(
            "SELECT 1 FROM bookings WHERE booking_id = $1", booking_id
        )
        if not exists:
            logger.warning("Skipping approval %s: booking not found", data.get("approval_id"))
            continue

        await conn.execute(
            """
            INSERT INTO approvals (
                approval_id, booking_id, student_chat_id, draft_content,
                action, confidence, status, reviewer, review_channel,
                tutor_message_id, created_at, resolved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (approval_id) DO NOTHING
            """,
            data.get("approval_id", path.stem),
            booking_id,
            data.get("student_chat_id", 0),
            data.get("draft_content", ""),
            data.get("action", "answer"),
            data.get("confidence"),
            data.get("status", "pending"),
            data.get("reviewer"),
            data.get("review_channel", "api"),
            data.get("tutor_message_id"),
            _parse_dt(data.get("created_at")) or datetime.now(timezone.utc),
            _parse_dt(data.get("resolved_at")),
        )
        count += 1

    logger.info("Imported %d approvals", count)
    return count


async def import_runtime_controls(conn: asyncpg.Connection, state_path: Path) -> None:
    path = state_path / "runtime_controls.json"
    if not path.exists():
        logger.info("No runtime_controls.json found, using defaults")
        return

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read runtime_controls.json: %s", exc)
        return

    await conn.execute(
        """
        UPDATE runtime_controls SET
            global_automation_enabled = $1,
            updated_by = $2,
            reason = $3,
            updated_at = now()
        WHERE id = 1
        """,
        data.get("global_automation_enabled", True),
        data.get("updated_by", "system"),
        data.get("reason"),
    )
    logger.info("Imported runtime controls")


async def import_idempotency(conn: asyncpg.Connection, state_path: Path) -> int:
    path = state_path / "idempotency.json"
    if not path.exists():
        return 0

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read idempotency.json: %s", exc)
        return 0

    now = time.time()
    count = 0
    for key, expiry in data.items():
        if expiry <= now:
            continue
        exp_dt = datetime.fromtimestamp(expiry, tz=timezone.utc)
        await conn.execute(
            """
            INSERT INTO idempotency_keys (key, expires_at) VALUES ($1, $2)
            ON CONFLICT (key) DO NOTHING
            """,
            key,
            exp_dt,
        )
        count += 1

    logger.info("Imported %d idempotency keys", count)
    return count


async def run_import(state_path: str, database_url: str) -> None:
    sp = Path(state_path)
    if not sp.exists():
        logger.error("State path %s does not exist", state_path)
        return

    conn = await asyncpg.connect(database_url)
    try:
        async with conn.transaction():
            await import_bookings(conn, sp)
            await import_conversations(conn, sp)
            await import_escalations(conn, sp)
            await import_approvals(conn, sp)
            await import_runtime_controls(conn, sp)
            await import_idempotency(conn, sp)
        logger.info("Import complete")
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(description="Import JSON state into PostgreSQL")
    parser.add_argument(
        "--state-path",
        default="/workspace/data/state",
        help="Path to the JSON state directory",
    )
    parser.add_argument(
        "--database-url",
        default="postgresql://bridge:bridge@localhost:5432/bridge",
        help="PostgreSQL connection URL",
    )
    args = parser.parse_args()
    asyncio.run(run_import(args.state_path, args.database_url))


if __name__ == "__main__":
    main()
