from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Telegram ──────────────────────────────────────────────────────────────
    # Student bot — open to all users, polled by Bridge
    telegram_bot_token_student: str
    # Owner bot — restricted to whitelist (TUTOR_CHAT_ID only).
    # Polled by OpenClaw; Bridge uses it only for sending escalation notices.
    telegram_bot_token_owner: str = ""
    telegram_mode: str = "polling"  # polling | webhook (webhook for production)
    telegram_api_base_url: str = ""

    # ── Planerka ──────────────────────────────────────────────────────────────
    planerka_api_key: str        # x-auth for REST API calls
    planerka_webhook_secret: str # Bearer secret on incoming webhooks

    # ── OpenClaw ──────────────────────────────────────────────────────────────
    gateway_auth_token: str      # OpenClaw gateway HTTP API
    openclaw_base_url: str = "http://openclaw:18789"
    openclaw_gateway_model: str = "openclaw"

    # ── Tutor API ─────────────────────────────────────────────────────────────
    tutor_api_token: str = ""    # Separate auth for /api/tutor/* endpoints
    tutor_chat_id: int | None = None

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "postgresql://bridge:bridge@postgres:5432/bridge"

    # ── Runtime paths ─────────────────────────────────────────────────────────
    state_path: str = "/workspace/data/state"
    uploads_path: str = "/workspace/data/uploads"
    knowledge_path: str = "/workspace/data/knowledge"
    audit_path: str = "/workspace/data/audit"
    log_path: str = "/workspace/data/logs"
    log_level: str = "INFO"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8081


@lru_cache
def get_settings() -> Settings:
    return Settings()
