"""
Prompt template loader.

Loads markdown templates from config/prompts/ and renders them
with ``{{key}}`` placeholder substitution.
"""

from __future__ import annotations

import re
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "config" / "prompts"
_cache: dict[str, str] = {}


def load_prompt(name: str) -> str:
    """Return raw template text for *name* (cached)."""
    if name in _cache:
        return _cache[name]

    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {path}")

    text = path.read_text(encoding="utf-8")
    _cache[name] = text
    return text


def render_prompt(name: str, **kwargs: str) -> str:
    """Load template *name* and replace ``{{key}}`` placeholders."""
    template = load_prompt(name)
    # Replace each {{key}} with the provided value
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key in kwargs:
            return kwargs[key]
        return match.group(0)  # leave unknown placeholders untouched

    return re.sub(r"\{\{(\w+)\}\}", _sub, template)


def reload() -> None:
    """Clear the template cache so files are re-read on next access."""
    _cache.clear()
