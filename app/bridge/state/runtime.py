"""
Runtime automation controls persisted outside per-chat conversation history.

Layout: {state_path}/runtime_controls.json
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path


def _runtime_path(state_path: str) -> Path:
    return Path(state_path) / "runtime_controls.json"


def _default_controls() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "global_automation_enabled": True,
        "updated_at": now,
        "updated_by": "system",
        "reason": None,
    }


async def load_controls(state_path: str) -> dict:
    path = _runtime_path(state_path)
    if not path.exists():
        return _default_controls()
    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
    data = json.loads(text)
    defaults = _default_controls()
    defaults.update(data)
    return defaults


async def save_controls(
    state_path: str,
    *,
    global_automation_enabled: bool,
    updated_by: str,
    reason: str | None = None,
) -> dict:
    data = {
        "global_automation_enabled": global_automation_enabled,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "updated_by": updated_by,
        "reason": reason,
    }
    path = _runtime_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    await asyncio.to_thread(
        path.write_text,
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return data
