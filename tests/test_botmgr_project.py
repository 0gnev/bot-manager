from __future__ import annotations

from pathlib import Path

from botmgr.project import read_env, write_env


def test_env_round_trip(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    values = {
        "LLM_PROVIDER": "ollama",
        "LLM_MODEL": "llama3.1:8b",
        "TELEGRAM_BOT_TOKEN_STUDENT": "123456:abc-DEF",
        "CUSTOM_EXTRA": "kept",
    }
    write_env(path, values)
    parsed = read_env(path)
    assert parsed["LLM_PROVIDER"] == "ollama"
    assert parsed["LLM_MODEL"] == "llama3.1:8b"
    assert parsed["TELEGRAM_BOT_TOKEN_STUDENT"] == "123456:abc-DEF"
    assert parsed["CUSTOM_EXTRA"] == "kept"
    assert (path.stat().st_mode & 0o777) == 0o600


def test_read_env_ignores_comments_and_blank_values(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# heading\n"
        "EMPTY=\n"
        "COMMENT_ONLY=             # just a comment\n"
        "INLINE=value  # trailing comment\n"
        "QUOTED=\"hash # inside\"\n",
        encoding="utf-8",
    )
    parsed = read_env(path)
    assert parsed["EMPTY"] == ""
    assert parsed["COMMENT_ONLY"] == ""
    assert parsed["INLINE"] == "value"
    assert parsed["QUOTED"] == "hash # inside"
