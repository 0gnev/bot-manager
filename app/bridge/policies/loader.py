"""
Load policy YAML files from config/policies/ and render them into a prompt block.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

_cache: dict[str, object] = {}
_mtimes: dict[str, float] = {}


def load_policies(path: str = "config/policies") -> dict:
    """Load all YAML files from *path*, returning a merged dict keyed by stem."""
    policies_dir = Path(path)
    if not policies_dir.is_dir():
        logger.warning("Policies directory not found: %s", path)
        return {}

    result: dict = {}
    for fp in sorted(policies_dir.glob("*.yaml")):
        mtime = fp.stat().st_mtime
        key = fp.stem
        if key in _cache and _mtimes.get(key) == mtime:
            result[key] = _cache[key]
            continue
        try:
            data = yaml.safe_load(fp.read_text(encoding="utf-8")) or {}
        except Exception as exc:
            logger.error("Failed to load policy %s: %s", fp, exc)
            continue
        _cache[key] = data
        _mtimes[key] = mtime
        result[key] = data
    return result


def reload_policies(path: str = "config/policies") -> dict:
    """Force-clear cache and reload all policies."""
    _cache.clear()
    _mtimes.clear()
    return load_policies(path)


def render_policy_block(policies: dict | None = None, path: str = "config/policies") -> str:
    """Render loaded policies into a text block suitable for prompt injection."""
    if policies is None:
        policies = load_policies(path)

    lines: list[str] = []

    # Response actions & output format
    actions = policies.get("response_actions", {})
    if actions.get("actions"):
        lines.append("Rules:")
        for name, info in actions["actions"].items():
            desc = info.get("description", "").strip()
            lines.append(f'- "{name}" \u2014 {desc}')
    if actions.get("output_format"):
        lines.append(actions["output_format"].strip())

    # Tone / communication rules
    tone = policies.get("tone", {})
    for rule in tone.get("rules", []):
        lines.append(f"- {rule}")

    # Confidence rules
    confidence = policies.get("confidence_thresholds", {})
    for rule in confidence.get("rules", []):
        lines.append(f"- {rule}")

    return "\n".join(lines)
