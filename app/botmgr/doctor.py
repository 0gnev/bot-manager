"""`botmgr doctor` — checks the whole setup end to end."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import httpx

from botmgr import providers as prov
from botmgr.project import read_env
from botmgr.theme import console, fail, ok, warn

_TIMEOUT = httpx.Timeout(8.0, connect=4.0)


def run(root: Path) -> int:
    console.print("\n[bold]Checking your Bot Manager setup…[/bold]\n")
    env = read_env(root / ".env")
    problems = 0

    problems += _check_docker()
    problems += _check_env(root, env)
    problems += _check_telegram(env)
    problems += _check_provider(env)
    problems += _check_vault(env, root)
    _check_bridge_health()

    console.print()
    if problems:
        fail(f"{problems} problem(s) found. Fix them and run [accent]botmgr doctor[/accent] again.")
        return 1
    ok("Everything looks good. Start the bot with [accent]botmgr up[/accent].")
    return 0


def _check_docker() -> int:
    if not shutil.which("docker"):
        fail("Docker is not installed — get it from docker.com/products/docker-desktop")
        return 1
    ok("Docker CLI found")
    result = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if result.returncode != 0:
        fail("Docker daemon is not running — start Docker Desktop (or the docker service)")
        return 1
    ok("Docker daemon is running")
    return 0


def _check_env(root: Path, env: dict[str, str]) -> int:
    if not (root / ".env").is_file():
        fail("No .env file — run [accent]botmgr setup[/accent] first")
        return 1
    ok(".env file present")
    return 0


def _check_telegram(env: dict[str, str]) -> int:
    problems = 0
    for label, key in (("student", "TELEGRAM_BOT_TOKEN_STUDENT"), ("tutor", "TELEGRAM_BOT_TOKEN_OWNER")):
        token = env.get(key, "")
        if not token:
            fail(f"Telegram {label} bot token is missing ({key})")
            problems += 1
            continue
        try:
            resp = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=_TIMEOUT)
            data = resp.json()
        except Exception as exc:
            warn(f"Could not reach Telegram to verify the {label} bot token ({exc})")
            continue
        if data.get("ok"):
            username = data["result"].get("username", "?")
            ok(f"Telegram {label} bot token is valid (@{username})")
        else:
            fail(f"Telegram rejected the {label} bot token — re-check it with @BotFather")
            problems += 1
    if not env.get("TUTOR_CHAT_ID", "").lstrip("-").isdigit():
        fail("TUTOR_CHAT_ID is missing or not numeric")
        problems += 1
    return problems


def _check_provider(env: dict[str, str]) -> int:
    provider = env.get("LLM_PROVIDER", "openai")
    info = prov.provider_by_id(provider)
    if info is None:
        fail(f"Unknown LLM_PROVIDER: {provider!r}")
        return 1

    if info.kind == "cloud":
        key = env.get(info.key_env, "") or env.get("LLM_API_KEY", "")
        if not key:
            fail(f"{info.title} needs an API key ({info.key_env})")
            return 1
        valid, message = prov.check_cloud_key(provider, key)
        if valid:
            ok(f"{info.title}: {message}")
            return 0
        fail(f"{info.title}: {message}")
        return 1

    if provider == "ollama":
        base = _localhost(env.get("OLLAMA_BASE_URL", "http://localhost:11434"))
        models = prov.list_ollama_models(base)
        if models is None:
            fail(f"Ollama is not reachable at {base}")
            return 1
        model = env.get("LLM_MODEL", "")
        if model and not any(m == model or m.startswith(model) for m in models):
            warn(f"Model {model!r} is not pulled yet — run: ollama pull {model}")
        ok(f"Ollama is running ({len(models)} model(s))")
        return 0

    base = _localhost(
        env.get("LMSTUDIO_BASE_URL", "http://localhost:1234/v1")
        if provider == "lmstudio"
        else env.get("LLM_BASE_URL", "")
    )
    if not base:
        fail("LLM_BASE_URL is required for a custom provider")
        return 1
    models = prov.list_openai_compat_models(base, env.get("LLM_API_KEY", ""))
    if models is None:
        fail(f"Model server is not reachable at {base}")
        return 1
    ok(f"Model server is up at {base} ({len(models)} model(s))")
    return 0


def _check_vault(env: dict[str, str], root: Path) -> int:
    vault = env.get("OBSIDIAN_VAULT_PATH", "")
    if not vault:
        ok(f"Knowledge base: built-in folder ({root / 'data' / 'knowledge'})")
        return 0
    path = Path(vault).expanduser()
    if not path.is_dir():
        fail(f"Obsidian vault folder does not exist: {path}")
        return 1
    notes = len(list(path.glob("**/*.md")))
    ok(f"Obsidian vault connected: {path} ({notes} markdown note(s))")
    return 0


def _check_bridge_health() -> None:
    try:
        resp = httpx.get("http://127.0.0.1:8081/health", timeout=httpx.Timeout(3.0))
        if resp.status_code == 200:
            ok("Bridge is running and healthy")
        else:
            warn(f"Bridge responded with HTTP {resp.status_code}")
    except Exception:
        console.print("[muted]· Bridge is not running (that's fine — start it with botmgr up)[/muted]")


def _localhost(url: str) -> str:
    return url.replace("host.docker.internal", "localhost")
