"""
Obsidian vault reader.

Reads markdown files from the knowledge mirror (data/knowledge/).
Used by openclaw to load context chunks for AI responses.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path


async def load_file(knowledge_path: str, relative: str) -> str | None:
    """Read a single markdown file by relative path."""
    path = Path(knowledge_path) / relative
    if not path.exists() or not path.is_file():
        return None
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def load_all(knowledge_path: str, glob: str = "**/*.md") -> list[dict]:
    """
    Load all matching markdown files.
    Returns list of {path, title, content}.
    """
    base = Path(knowledge_path)
    results = []

    def _read_all() -> list[dict]:
        items = []
        for p in sorted(base.glob(glob)):
            if p.name.startswith("."):
                continue
            text = p.read_text(encoding="utf-8")
            title = _extract_title(text) or p.stem
            items.append(
                {
                    "path": str(p.relative_to(base)),
                    "title": title,
                    "content": text,
                }
            )
        return items

    return await asyncio.to_thread(_read_all)


async def search(knowledge_path: str, query: str, limit: int = 5) -> list[dict]:
    """
    Naive keyword search across all markdown files.
    Returns the most relevant chunks (whole files for now).
    """
    query_lower = query.lower()
    all_docs = await load_all(knowledge_path)
    scored = []
    for doc in all_docs:
        text_lower = doc["content"].lower()
        score = sum(text_lower.count(word) for word in query_lower.split())
        if score > 0:
            scored.append((score, doc))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [doc for _, doc in scored[:limit]]


def _extract_title(content: str) -> str | None:
    """Extract first H1 heading from markdown."""
    match = re.search(r"^#\s+(.+)", content, re.MULTILINE)
    return match.group(1).strip() if match else None
