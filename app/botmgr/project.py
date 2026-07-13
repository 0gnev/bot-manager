"""Project root resolution and .env reading/writing."""

from __future__ import annotations

import os
import re
from pathlib import Path

_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class ProjectNotFound(RuntimeError):
    pass


def find_root(start: Path | None = None) -> Path:
    """Walk up from *start* (default cwd) until a compose.yaml is found.

    BOTMGR_HOME overrides the search entirely.
    """
    override = os.environ.get("BOTMGR_HOME")
    if override:
        root = Path(override).expanduser()
        if not (root / "compose.yaml").is_file():
            raise ProjectNotFound(f"BOTMGR_HOME={override} does not contain compose.yaml")
        return root

    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "compose.yaml").is_file() and (candidate / ".env.example").is_file():
            return candidate
    raise ProjectNotFound(
        "Not inside a Bot Manager checkout. Run from the project folder, "
        "or set BOTMGR_HOME=/path/to/bot-manager."
    )


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file into a dict. Inline comments after values are dropped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2).strip()
        if raw.startswith(("'", '"')) and raw.endswith(raw[0]) and len(raw) >= 2:
            value = raw[1:-1]
        else:
            value = raw.split(" #", 1)[0].strip()
            if value.startswith("#"):  # bare comment after `KEY=`
                value = ""
        values[key] = value
    return values


def write_env(path: Path, values: dict[str, str]) -> None:
    """Write .env in a stable, commented layout. Unknown keys are appended."""
    known: list[tuple[str, list[str]]] = [
        ("General", ["PROJECT_NAME", "TZ"]),
        (
            "AI / LLM providers",
            [
                "LLM_PROVIDER",
                "LLM_MODEL",
                "LLM_FALLBACKS",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "OPENROUTER_API_KEY",
                "LLM_BASE_URL",
                "LLM_API_KEY",
                "OLLAMA_BASE_URL",
                "LMSTUDIO_BASE_URL",
            ],
        ),
        (
            "Telegram",
            ["TELEGRAM_BOT_TOKEN_STUDENT", "TELEGRAM_BOT_TOKEN_OWNER", "TELEGRAM_MODE", "TUTOR_CHAT_ID"],
        ),
        ("Planerka", ["PLANERKA_BASE_URL", "PLANERKA_API_KEY", "PLANERKA_WEBHOOK_SECRET"]),
        ("Tutor API", ["TUTOR_API_TOKEN"]),
        ("Knowledge (Obsidian)", ["OBSIDIAN_VAULT_PATH"]),
        ("Database", ["DATABASE_URL"]),
        ("Runtime storage", ["AUDIT_PATH", "LOG_PATH", "LOG_LEVEL"]),
    ]

    remaining = dict(values)
    lines: list[str] = []
    for section, keys in known:
        section_lines = []
        for key in keys:
            if key in remaining:
                section_lines.append(f"{key}={remaining.pop(key)}")
        if section_lines:
            lines.append(f"# ── {section} " + "─" * max(4, 60 - len(section)))
            lines.extend(section_lines)
            lines.append("")

    if remaining:
        lines.append("# ── Other " + "─" * 51)
        for key in sorted(remaining):
            lines.append(f"{key}={remaining[key]}")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
