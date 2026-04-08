from __future__ import annotations

from datetime import datetime

from bridge.timezones import format_datetime, validate_time_zone_name


def test_format_datetime_converts_to_requested_time_zone() -> None:
    assert format_datetime(
        "2026-04-07T16:00:00+00:00",
        time_zone_name="Europe/Moscow",
        fmt="%d.%m.%Y %H:%M",
    ) == "07.04.2026 19:00"


def test_format_datetime_treats_naive_values_as_utc() -> None:
    assert format_datetime(
        datetime(2026, 4, 7, 16, 0),
        time_zone_name="Asia/Bishkek",
        fmt="%d.%m.%Y %H:%M",
    ) == "07.04.2026 22:00"


def test_validate_time_zone_name_rejects_invalid_values() -> None:
    assert validate_time_zone_name("Europe/Moscow") == "Europe/Moscow"
    assert validate_time_zone_name("Mars/Olympus") is None
