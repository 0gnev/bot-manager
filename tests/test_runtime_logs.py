from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bridge.runtime_logs import JsonLineFormatter, configure_runtime_logging, read_recent_runtime_logs


def test_json_line_formatter_emits_expected_fields() -> None:
    formatter = JsonLineFormatter()
    record = SimpleNamespace(
        levelname="INFO",
        name="bridge.test",
        module="test_mod",
        lineno=123,
        exc_info=None,
        getMessage=lambda: "hello world",
    )

    payload = json.loads(formatter.format(record))

    assert payload["level"] == "INFO"
    assert payload["logger"] == "bridge.test"
    assert payload["module"] == "test_mod"
    assert payload["line"] == 123
    assert payload["message"] == "hello world"
    assert "timestamp" in payload


def test_read_recent_runtime_logs_filters_entries(tmp_path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    log_file = log_dir / "bridge.log"
    lines = [
        {"timestamp": "2026-04-06T01:00:00+00:00", "level": "INFO", "logger": "a", "message": "first"},
        {"timestamp": "2026-04-06T01:01:00+00:00", "level": "ERROR", "logger": "b", "message": "second failed"},
        {"timestamp": "2026-04-06T01:02:00+00:00", "level": "INFO", "logger": "a", "message": "third"},
    ]
    log_file.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")

    recent = asyncio.run(
        read_recent_runtime_logs(
            log_path=str(log_dir),
            limit=2,
            logger_name="a",
            level="INFO",
        )
    )

    assert [entry["message"] for entry in recent] == ["third", "first"]


def test_configure_runtime_logging_creates_rotating_file(tmp_path) -> None:
    settings = SimpleNamespace(
        log_path=str(tmp_path / "logs"),
        log_level="INFO",
        log_max_bytes=1024,
        log_backup_count=2,
    )

    configure_runtime_logging(settings)

    import logging

    logging.getLogger("bridge.test").info("configured")

    assert (tmp_path / "logs" / "bridge.log").exists()
