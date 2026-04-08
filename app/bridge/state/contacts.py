"""
Contact state helpers backed by PostgreSQL.
"""

from __future__ import annotations

from bridge.db import get_pool


def _row_to_dict(row) -> dict:
    data = dict(row)
    for ts_field in ("created_at", "updated_at"):
        val = data.get(ts_field)
        if val is not None and hasattr(val, "isoformat"):
            data[ts_field] = val.isoformat()
    return data


async def load(state_path: str, contact_id: int) -> dict | None:
    row = await get_pool().fetchrow("SELECT * FROM contacts WHERE id = $1", contact_id)
    if row is None:
        return None
    return _row_to_dict(row)


async def load_by_telegram_user(state_path: str, telegram_user_id: int) -> dict | None:
    row = await get_pool().fetchrow(
        "SELECT * FROM contacts WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def load_by_telegram_username(state_path: str, telegram_username: str) -> dict | None:
    normalized = (telegram_username or "").lstrip("@")
    row = await get_pool().fetchrow(
        """
        SELECT * FROM contacts
        WHERE lower(telegram_username) = lower($1)
        LIMIT 1
        """,
        normalized,
    )
    if row is None:
        return None
    return _row_to_dict(row)


async def find_all_with_active_bookings_by_telegram_username(
    state_path: str,
    telegram_username: str,
) -> list[dict]:
    normalized = (telegram_username or "").lstrip("@")
    if not normalized:
        return []
    rows = await get_pool().fetch(
        """
        SELECT DISTINCT c.*
        FROM contacts c
        JOIN bookings b ON b.contact_id = c.id
        WHERE b.status = 'active'
          AND lower(c.telegram_username) = lower($1)
        ORDER BY c.updated_at DESC, c.id DESC
        """,
        normalized,
    )
    return [_row_to_dict(row) for row in rows]


async def search_dialog_targets(
    state_path: str,
    query: str,
    *,
    limit: int = 5,
) -> list[dict]:
    normalized = (query or "").strip()
    if not normalized:
        return []

    like_value = f"%{normalized.lower()}%"
    rows = await get_pool().fetch(
        """
        SELECT *,
               CASE
                   WHEN lower(COALESCE(telegram_username, '')) = lower($1) THEN 400
                   WHEN lower(COALESCE(email, '')) = lower($1) THEN 350
                   WHEN lower(COALESCE(name, '')) = lower($1) THEN 300
                   WHEN lower(COALESCE(telegram_username, '')) LIKE $2 THEN 220
                   WHEN lower(COALESCE(email, '')) LIKE $2 THEN 200
                   WHEN lower(COALESCE(name, '')) LIKE $2 THEN 180
                   ELSE 0
               END AS match_rank
        FROM contacts
        WHERE lower(COALESCE(telegram_username, '')) = lower($1)
           OR lower(COALESCE(email, '')) = lower($1)
           OR lower(COALESCE(name, '')) = lower($1)
           OR lower(COALESCE(telegram_username, '')) LIKE $2
           OR lower(COALESCE(email, '')) LIKE $2
           OR lower(COALESCE(name, '')) LIKE $2
        ORDER BY match_rank DESC, updated_at DESC, id DESC
        LIMIT $3
        """,
        normalized,
        like_value,
        limit,
    )
    return [_row_to_dict(row) for row in rows]


async def ensure_telegram_contact(
    state_path: str,
    telegram_user_id: int,
    *,
    telegram_username: str | None = None,
    name: str | None = None,
) -> dict:
    normalized_username = (telegram_username or "").lstrip("@") or None
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM contacts WHERE telegram_user_id = $1",
                telegram_user_id,
            )

            if row is None:
                row = await conn.fetchrow(
                    """
                    INSERT INTO contacts (telegram_user_id, telegram_username, name)
                    VALUES ($1, $2, $3)
                    RETURNING *
                    """,
                    telegram_user_id,
                    normalized_username,
                    name,
                )
                return _row_to_dict(row)

            row = await conn.fetchrow(
                """
                UPDATE contacts
                SET telegram_user_id = COALESCE($2, telegram_user_id),
                    telegram_username = COALESCE($3, telegram_username),
                    name = COALESCE($4, name),
                    updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                row["id"],
                telegram_user_id,
                normalized_username,
                name,
            )
            return _row_to_dict(row)


async def attach_telegram_identity(
    state_path: str,
    contact_id: int,
    *,
    telegram_user_id: int,
    telegram_username: str | None = None,
    name: str | None = None,
) -> dict | None:
    normalized_username = (telegram_username or "").lstrip("@") or None
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            target = await conn.fetchrow(
                "SELECT * FROM contacts WHERE id = $1 FOR UPDATE",
                contact_id,
            )
            if target is None:
                return None

            keeper = await conn.fetchrow(
                "SELECT * FROM contacts WHERE telegram_user_id = $1 FOR UPDATE",
                telegram_user_id,
            )
            if keeper is not None and keeper["id"] != contact_id:
                keeper_id = keeper["id"]
                await conn.execute(
                    "UPDATE bookings SET contact_id = $1 WHERE contact_id = $2",
                    keeper_id,
                    contact_id,
                )
                for table_name in ("messages", "attachments", "escalations", "approvals"):
                    await conn.execute(
                        f"UPDATE {table_name} SET contact_id = $1 WHERE contact_id = $2",
                        keeper_id,
                        contact_id,
                    )
                row = await conn.fetchrow(
                    """
                    UPDATE contacts
                    SET telegram_username = COALESCE($2, telegram_username, $3),
                        name = COALESCE(name, $4, $5),
                        email = COALESCE(email, $6),
                        phone = COALESCE(phone, $7),
                        time_zone = COALESCE(time_zone, $8),
                        active_booking_id = COALESCE(active_booking_id, $9),
                        updated_at = now()
                    WHERE id = $1
                    RETURNING *
                    """,
                    keeper_id,
                    normalized_username,
                    target["telegram_username"],
                    name,
                    target["name"],
                        target["email"],
                        target["phone"],
                        target["time_zone"],
                        target["active_booking_id"],
                    )
                await conn.execute("DELETE FROM contacts WHERE id = $1", contact_id)
                return _row_to_dict(row) if row is not None else None

            row = await conn.fetchrow(
                """
                UPDATE contacts
                SET telegram_user_id = COALESCE($2, telegram_user_id),
                    telegram_username = COALESCE($3, telegram_username),
                    name = COALESCE($4, name),
                    updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                contact_id,
                telegram_user_id,
                normalized_username,
                name,
            )
            if row is None:
                return None
            return _row_to_dict(row)


async def set_active_booking(
    state_path: str,
    contact_id: int,
    booking_id: str | None,
) -> dict | None:
    row = await get_pool().fetchrow(
        """
        UPDATE contacts
        SET active_booking_id = $2,
            updated_at = now()
        WHERE id = $1
        RETURNING *
        """,
        contact_id,
        booking_id,
    )
    if row is None:
        return None
    return _row_to_dict(row)
