from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def validate_time_zone_name(value: str | None) -> str | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    try:
        ZoneInfo(normalized)
    except Exception:
        return None
    return normalized


def get_time_zone(value: str | None) -> ZoneInfo | None:
    normalized = validate_time_zone_name(value)
    if not normalized:
        return None
    return ZoneInfo(normalized)


def ensure_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt


def parse_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return ensure_aware(value)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        normalized = normalized.replace("Z", "+00:00")
        try:
            return ensure_aware(datetime.fromisoformat(normalized))
        except ValueError:
            return None
    return None


def convert_datetime(
    value: datetime | str | None,
    *,
    time_zone_name: str | None = None,
) -> datetime | None:
    dt = parse_datetime(value)
    if dt is None:
        return None

    zone = get_time_zone(time_zone_name)
    if zone is None:
        return dt
    return dt.astimezone(zone)


def format_datetime(
    value: datetime | str | None,
    *,
    time_zone_name: str | None = None,
    fmt: str = "%d %b %Y, %H:%M",
    fallback: str = "—",
    include_time_zone: bool = False,
) -> str:
    dt = convert_datetime(value, time_zone_name=time_zone_name)
    if dt is None:
        return fallback

    text = dt.strftime(fmt)
    if not include_time_zone:
        return text

    label = validate_time_zone_name(time_zone_name) or dt.tzname()
    if not label:
        return text
    return f"{text} ({label})"
