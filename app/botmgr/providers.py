"""Provider metadata and connectivity probes for the wizard and doctor."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

_TIMEOUT = httpx.Timeout(8.0, connect=4.0)


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    title: str
    kind: str  # cloud | local | custom
    key_env: str = ""
    default_model: str = ""
    hint: str = ""


PROVIDERS: list[ProviderInfo] = [
    ProviderInfo(
        "openai", "OpenAI", "cloud",
        key_env="OPENAI_API_KEY", default_model="gpt-5-mini",
        hint="API key from platform.openai.com",
    ),
    ProviderInfo(
        "anthropic", "Anthropic (Claude)", "cloud",
        key_env="ANTHROPIC_API_KEY", default_model="claude-haiku-4-5",
        hint="API key from console.anthropic.com",
    ),
    ProviderInfo(
        "openrouter", "OpenRouter (many models, one key)", "cloud",
        key_env="OPENROUTER_API_KEY", default_model="openai/gpt-5-mini",
        hint="API key from openrouter.ai",
    ),
    ProviderInfo(
        "ollama", "Ollama (local, free)", "local",
        default_model="llama3.1:8b",
        hint="needs the Ollama app running on this machine",
    ),
    ProviderInfo(
        "lmstudio", "LM Studio (local, free)", "local",
        default_model="",
        hint="needs the LM Studio local server running",
    ),
    ProviderInfo(
        "custom", "Custom OpenAI-compatible URL", "custom",
        hint="vLLM, llama.cpp server, a remote gateway, …",
    ),
]


def provider_by_id(provider_id: str) -> ProviderInfo | None:
    return next((p for p in PROVIDERS if p.id == provider_id), None)


# ── Probes (run on the host, so use localhost URLs) ─────────────────────────


def list_ollama_models(base_url: str = "http://localhost:11434") -> list[str] | None:
    """Return installed model names, or None when the server is unreachable."""
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=_TIMEOUT)
        resp.raise_for_status()
        models = resp.json().get("models") or []
        return [m.get("name", "") for m in models if m.get("name")]
    except Exception:
        return None


def list_openai_compat_models(base_url: str, api_key: str = "") -> list[str] | None:
    """List models from an OpenAI-compatible server (LM Studio, vLLM, OpenAI…)."""
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        resp = httpx.get(f"{base_url.rstrip('/')}/models", headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json().get("data") or []
        return [m.get("id", "") for m in data if m.get("id")]
    except Exception:
        return None


def check_cloud_key(provider_id: str, api_key: str) -> tuple[bool, str]:
    """Cheap authenticated call to verify an API key actually works."""
    try:
        if provider_id == "anthropic":
            resp = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout=_TIMEOUT,
            )
        elif provider_id == "openrouter":
            resp = httpx.get(
                "https://openrouter.ai/api/v1/key",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
        else:  # openai
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=_TIMEOUT,
            )
    except Exception as exc:
        return False, f"network error: {exc}"
    if resp.status_code == 401:
        return False, "the API key was rejected (401)"
    if resp.status_code >= 400:
        return False, f"unexpected HTTP {resp.status_code}"
    return True, "key accepted"


def host_url_for_container(url: str) -> str:
    """Translate a localhost URL (probed on the host) into one the container can reach."""
    return url.replace("localhost", "host.docker.internal").replace(
        "127.0.0.1", "host.docker.internal"
    )
