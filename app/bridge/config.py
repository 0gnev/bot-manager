from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env.local",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    telegram_bot_token_default: str
    telegram_bot_token_manager: str
    telegram_mode: str = "polling"

    # Planerka
    planerka_api_key: str        # x-auth for REST API calls
    planerka_webhook_secret: str # Bearer secret on incoming webhooks

    # Openclaw
    gateway_auth_token: str
    openclaw_base_url: str = "http://openclaw:18789"

    # Tutor
    tutor_chat_id: int | None = None

    # Runtime paths
    state_path: str = "/workspace/data/state"
    uploads_path: str = "/workspace/data/uploads"
    knowledge_path: str = "/workspace/data/knowledge"

    # Server
    host: str = "0.0.0.0"
    port: int = 8081


@lru_cache
def get_settings() -> Settings:
    return Settings()
