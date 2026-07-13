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
    # Polled by Bridge for tutor/operator actions.
    telegram_bot_token_owner: str = ""
    telegram_mode: str = "polling"  # polling | webhook (webhook for production)
    telegram_api_base_url: str = ""

    # ── Planerka ──────────────────────────────────────────────────────────────
    planerka_api_key: str        # x-auth for REST API calls
    planerka_webhook_secret: str # Bearer secret on incoming webhooks

    # ── AI / LLM providers ────────────────────────────────────────────────────
    # Primary provider: openai | openrouter | anthropic | ollama | lmstudio | custom
    llm_provider: str = "openai"
    llm_model: str = ""          # empty → per-provider default
    llm_base_url: str = ""       # override for cloud providers; required for "custom"
    llm_api_key: str = ""        # generic key override (used when the provider key is empty)
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    openrouter_api_key: str = ""
    # Local model servers (host.docker.internal reaches the host from the container)
    ollama_base_url: str = "http://host.docker.internal:11434"
    lmstudio_base_url: str = "http://host.docker.internal:1234/v1"
    # Failover chain: comma-separated "provider:model", e.g. "ollama:llama3.1:8b"
    llm_fallbacks: str = ""
    llm_max_tokens: int = 2048
    llm_temperature: float | None = None
    llm_request_attempts: int = 3
    llm_request_backoff_seconds: float = 0.5

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
    telegram_delivery_attempts: int = 3
    telegram_delivery_backoff_seconds: float = 0.5

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8081


@lru_cache
def get_settings() -> Settings:
    return Settings()
