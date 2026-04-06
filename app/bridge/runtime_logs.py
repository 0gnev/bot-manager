"""
Runtime logging for the Bridge process.

Unlike the audit log, this captures technical execution logs from bridge,
aiogram, uvicorn, and related modules into a rotating JSON-lines file.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class JsonLineFormatter(logging.Formatter):
    """Serialize one LogRecord as a compact JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "module": record.module,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_runtime_logging(settings) -> None:
    """Configure root logging for console + rotating runtime file."""
    log_dir = Path(settings.log_path)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "bridge.log"

    level_name = str(getattr(settings, "log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=int(getattr(settings, "log_max_bytes", 10 * 1024 * 1024)),
        backupCount=int(getattr(settings, "log_backup_count", 5)),
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(JsonLineFormatter())

    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.captureWarnings(True)


async def read_recent_runtime_logs(
    *,
    log_path: str,
    limit: int = 200,
    logger_name: str | None = None,
    level: str | None = None,
    contains: str | None = None,
) -> list[dict[str, Any]]:
    """Read recent runtime log entries from the current bridge log file."""

    def _read() -> list[dict[str, Any]]:
        path = Path(log_path) / "bridge.log"
        if not path.exists():
            return []

        level_filter = level.upper() if level else None
        contains_filter = contains.lower() if contains else None
        entries: deque[dict[str, Any]] = deque(maxlen=limit)

        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if logger_name and entry.get("logger") != logger_name:
                    continue
                if level_filter and str(entry.get("level", "")).upper() != level_filter:
                    continue
                if contains_filter and contains_filter not in str(entry.get("message", "")).lower():
                    continue
                entries.append(entry)

        return list(entries)[::-1]

    from asyncio import to_thread

    return await to_thread(_read)
