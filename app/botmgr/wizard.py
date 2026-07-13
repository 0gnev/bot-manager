"""Interactive `botmgr setup` wizard.

Walks through Telegram, AI provider, Obsidian and Planerka configuration and
writes a complete .env. Re-running keeps previous answers as defaults.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path

from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from botmgr import providers as prov
from botmgr.project import read_env, write_env
from botmgr.theme import banner, console, fail, ok, step, warn

_TOKEN_RE = re.compile(r"^\d+:[\w-]+$")
_TOTAL_STEPS = 5


def run(root: Path) -> int:
    env_path = root / ".env"
    env = read_env(root / ".env.example")
    env.update(read_env(env_path))

    banner("interactive setup")
    console.print(
        "\nAnswers are saved to [key].env[/key] in the project folder. "
        "Press [key]Enter[/key] to keep the value shown in brackets.\n",
    )

    _step_telegram(env)
    _step_ai(env)
    _step_obsidian(env, root)
    _step_planerka(env)
    _step_finish(env)

    write_env(env_path, env)
    console.print()
    ok(f"Configuration written to [key]{env_path}[/key] (permissions 600)")
    _summary(env)
    console.print(
        "\nNext: [accent]botmgr doctor[/accent] to verify everything, "
        "then [accent]botmgr up[/accent] to start the bot.\n"
    )
    return 0


# ── Steps ────────────────────────────────────────────────────────────────────


def _step_telegram(env: dict[str, str]) -> None:
    step(1, _TOTAL_STEPS, "Telegram bots")
    console.print(
        "[muted]Two bots from @BotFather: a public one students write to, and a\n"
        "private one where you (the tutor) approve answers and get escalations.[/muted]\n"
    )
    env["TELEGRAM_BOT_TOKEN_STUDENT"] = _ask_token("Student bot token", env.get("TELEGRAM_BOT_TOKEN_STUDENT", ""))
    env["TELEGRAM_BOT_TOKEN_OWNER"] = _ask_token("Tutor (owner) bot token", env.get("TELEGRAM_BOT_TOKEN_OWNER", ""))

    current_chat = env.get("TUTOR_CHAT_ID", "")
    console.print("[muted]Your numeric Telegram ID — @userinfobot can tell you.[/muted]")
    while True:
        raw = Prompt.ask("Tutor chat ID", default=current_chat or None)
        if raw and raw.strip().lstrip("-").isdigit():
            env["TUTOR_CHAT_ID"] = raw.strip()
            break
        fail("Expected a numeric ID like 123456789")
    env.setdefault("TELEGRAM_MODE", "polling")


def _step_ai(env: dict[str, str]) -> None:
    step(2, _TOTAL_STEPS, "AI model")
    table = Table(show_header=False, box=None, padding=(0, 2))
    for index, info in enumerate(prov.PROVIDERS, start=1):
        table.add_row(f"[accent]{index}[/accent]", f"[bold]{info.title}[/bold]", f"[muted]{info.hint}[/muted]")
    console.print(table)

    default_choice = next(
        (str(i) for i, p in enumerate(prov.PROVIDERS, start=1) if p.id == env.get("LLM_PROVIDER")),
        "1",
    )
    choice = IntPrompt.ask(
        "Primary provider",
        choices=[str(i) for i in range(1, len(prov.PROVIDERS) + 1)],
        default=int(default_choice),
    )
    info = prov.PROVIDERS[choice - 1]
    env["LLM_PROVIDER"] = info.id

    if info.kind == "cloud":
        _configure_cloud(env, info)
    elif info.id == "ollama":
        _configure_ollama(env)
    elif info.id == "lmstudio":
        _configure_lmstudio(env)
    else:
        _configure_custom(env)

    _configure_fallback(env, primary=info)


def _configure_cloud(env: dict[str, str], info: prov.ProviderInfo) -> None:
    console.print(f"[muted]{info.hint}[/muted]")
    while True:
        key = Prompt.ask(
            f"{info.title} API key",
            password=True,
            default=env.get(info.key_env) or None,
            show_default=False,
        )
        if not key:
            fail("An API key is required for a cloud provider")
            continue
        console.print("[muted]Checking the key…[/muted]")
        valid, message = prov.check_cloud_key(info.id, key)
        if valid:
            ok(message)
            env[info.key_env] = key
            break
        warn(f"Could not verify: {message}")
        if Confirm.ask("Use this key anyway?", default=False):
            env[info.key_env] = key
            break
    env["LLM_MODEL"] = Prompt.ask(
        "Model", default=env.get("LLM_MODEL") or info.default_model
    )


def _configure_ollama(env: dict[str, str]) -> None:
    models = prov.list_ollama_models()
    if models is None:
        warn("Ollama is not reachable on localhost:11434.")
        console.print("[muted]Install from ollama.com, then: ollama pull llama3.1:8b[/muted]")
        env["LLM_MODEL"] = Prompt.ask("Model to use once Ollama is running", default=env.get("LLM_MODEL") or "llama3.1:8b")
    elif not models:
        warn("Ollama is running but has no models. Try: ollama pull llama3.1:8b")
        env["LLM_MODEL"] = Prompt.ask("Model to use after pulling", default=env.get("LLM_MODEL") or "llama3.1:8b")
    else:
        ok(f"Ollama is running with {len(models)} model(s)")
        env["LLM_MODEL"] = _pick_model(models, env.get("LLM_MODEL", ""))
    env.setdefault("OLLAMA_BASE_URL", "http://host.docker.internal:11434")


def _configure_lmstudio(env: dict[str, str]) -> None:
    models = prov.list_openai_compat_models("http://localhost:1234/v1")
    if not models:
        warn("LM Studio server is not reachable on localhost:1234.")
        console.print("[muted]In LM Studio: Developer tab → Start server, load a model.[/muted]")
        env["LLM_MODEL"] = Prompt.ask("Model name to use once it is running", default=env.get("LLM_MODEL") or "local-model")
    else:
        ok(f"LM Studio is running with {len(models)} model(s)")
        env["LLM_MODEL"] = _pick_model(models, env.get("LLM_MODEL", ""))
    env.setdefault("LMSTUDIO_BASE_URL", "http://host.docker.internal:1234/v1")


def _configure_custom(env: dict[str, str]) -> None:
    console.print("[muted]Any server with an OpenAI-compatible /chat/completions endpoint.[/muted]")
    while True:
        url = Prompt.ask("Base URL (e.g. http://192.168.1.10:8000/v1)", default=env.get("LLM_BASE_URL") or None)
        if url and url.startswith(("http://", "https://")):
            break
        fail("Expected an http(s):// URL")
    env["LLM_BASE_URL"] = prov.host_url_for_container(url)
    env["LLM_API_KEY"] = Prompt.ask(
        "API key (Enter if none)", password=True, default=env.get("LLM_API_KEY", ""), show_default=False
    )
    models = prov.list_openai_compat_models(url, env["LLM_API_KEY"])
    if models:
        ok(f"Server is reachable with {len(models)} model(s)")
        env["LLM_MODEL"] = _pick_model(models, env.get("LLM_MODEL", ""))
    else:
        warn("Could not list models (the server may still work).")
        env["LLM_MODEL"] = Prompt.ask("Model name", default=env.get("LLM_MODEL") or "default")


def _configure_fallback(env: dict[str, str], *, primary: prov.ProviderInfo) -> None:
    if primary.kind == "cloud":
        question = "Add a local fallback model (used when the cloud API is down)?"
        candidates = ["ollama", "lmstudio"]
    else:
        question = "Add a cloud fallback model (used when the local server is down)?"
        candidates = ["openai", "anthropic", "openrouter"]

    if not Confirm.ask(question, default=bool(env.get("LLM_FALLBACKS"))):
        env["LLM_FALLBACKS"] = ""
        return

    options = [prov.provider_by_id(c) for c in candidates]
    table = Table(show_header=False, box=None, padding=(0, 2))
    for index, info in enumerate(options, start=1):
        table.add_row(f"[accent]{index}[/accent]", info.title)
    console.print(table)
    choice = IntPrompt.ask("Fallback provider", choices=[str(i) for i in range(1, len(options) + 1)], default=1)
    info = options[choice - 1]

    if info.kind == "cloud" and not env.get(info.key_env):
        _configure_cloud_key_only(env, info)

    if info.id == "ollama":
        models = prov.list_ollama_models() or []
        model = _pick_model(models, "") if models else Prompt.ask("Fallback model", default=info.default_model)
    else:
        model = Prompt.ask("Fallback model", default=info.default_model or "local-model")
    env["LLM_FALLBACKS"] = f"{info.id}:{model}"
    ok(f"Failover chain: {env['LLM_PROVIDER']}:{env.get('LLM_MODEL', '?')} → {env['LLM_FALLBACKS']}")


def _configure_cloud_key_only(env: dict[str, str], info: prov.ProviderInfo) -> None:
    key = Prompt.ask(f"{info.title} API key", password=True, show_default=False)
    if key:
        env[info.key_env] = key


def _step_obsidian(env: dict[str, str], root: Path) -> None:
    step(3, _TOTAL_STEPS, "Knowledge base (Obsidian)")
    console.print(
        "[muted]Point the bot at an Obsidian vault (or any folder of markdown notes).\n"
        "The bot reads answers from it and writes bookings, conversations and\n"
        "approved knowledge notes back — everything stays plain markdown.\n"
        "Tip: use a dedicated subfolder of your vault, e.g. ~/Vault/TutorBot.[/muted]\n"
    )
    default_path = env.get("OBSIDIAN_VAULT_PATH", "")
    while True:
        raw = Prompt.ask(
            "Vault folder (Enter to use the built-in ./data/knowledge)",
            default=default_path,
            show_default=bool(default_path),
        )
        if not raw:
            env["OBSIDIAN_VAULT_PATH"] = ""
            ok(f"Using built-in knowledge folder: {root / 'data' / 'knowledge'}")
            return
        path = Path(raw).expanduser()
        if path.is_dir():
            env["OBSIDIAN_VAULT_PATH"] = str(path)
            ok(f"Vault connected: {path}")
            return
        if Confirm.ask(f"[warn]{path}[/warn] does not exist. Create it?", default=True):
            path.mkdir(parents=True, exist_ok=True)
            env["OBSIDIAN_VAULT_PATH"] = str(path)
            ok(f"Vault folder created: {path}")
            return


def _step_planerka(env: dict[str, str]) -> None:
    step(4, _TOTAL_STEPS, "Planerka bookings (optional)")
    if not Confirm.ask("Connect Planerka booking webhooks?", default=bool(env.get("PLANERKA_API_KEY"))):
        env.setdefault("PLANERKA_BASE_URL", "")
        env["PLANERKA_API_KEY"] = env.get("PLANERKA_API_KEY") or "disabled"
        env["PLANERKA_WEBHOOK_SECRET"] = env.get("PLANERKA_WEBHOOK_SECRET") or secrets.token_hex(16)
        return
    env["PLANERKA_BASE_URL"] = Prompt.ask("Planerka base URL", default=env.get("PLANERKA_BASE_URL", ""))
    env["PLANERKA_API_KEY"] = Prompt.ask("Planerka API key", password=True, default=env.get("PLANERKA_API_KEY", ""), show_default=False)
    current_secret = env.get("PLANERKA_WEBHOOK_SECRET") or secrets.token_hex(16)
    env["PLANERKA_WEBHOOK_SECRET"] = Prompt.ask(
        "Webhook secret (shared with Planerka)", default=current_secret
    )


def _step_finish(env: dict[str, str]) -> None:
    step(5, _TOTAL_STEPS, "Finishing touches")
    env["TZ"] = Prompt.ask("Time zone", default=env.get("TZ") or "UTC")
    if not env.get("TUTOR_API_TOKEN"):
        env["TUTOR_API_TOKEN"] = secrets.token_hex(24)
        ok("Generated a tutor API token")
    env.setdefault("PROJECT_NAME", "bot-manager")
    env.setdefault("DATABASE_URL", "postgresql://bridge:bridge@postgres:5432/bridge")
    env.setdefault("AUDIT_PATH", "/workspace/data/audit")
    env.setdefault("LOG_PATH", "/workspace/data/logs")
    env.setdefault("LOG_LEVEL", "INFO")


# ── Helpers ──────────────────────────────────────────────────────────────────


def _ask_token(label: str, current: str) -> str:
    while True:
        token = Prompt.ask(
            label,
            password=True,
            default=current or None,
            show_default=False,
        )
        if token and _TOKEN_RE.match(token):
            return token
        if token and Confirm.ask("That does not look like a bot token (123456:ABC…). Use it anyway?", default=False):
            return token
        if not token:
            fail("A token is required — get one from @BotFather")


def _pick_model(models: list[str], current: str) -> str:
    show = models[:15]
    table = Table(show_header=False, box=None, padding=(0, 2))
    for index, name in enumerate(show, start=1):
        table.add_row(f"[accent]{index}[/accent]", name)
    console.print(table)
    default = str(show.index(current) + 1) if current in show else "1"
    choice = IntPrompt.ask("Model", choices=[str(i) for i in range(1, len(show) + 1)], default=int(default))
    return show[choice - 1]


def _summary(env: dict[str, str]) -> None:
    table = Table(title="Configuration", title_style="accent", border_style="grey35")
    table.add_column("Setting", style="key")
    table.add_column("Value", style="value")
    provider = env.get("LLM_PROVIDER", "")
    model = env.get("LLM_MODEL") or "(provider default)"
    chain = f"{provider}:{model}"
    if env.get("LLM_FALLBACKS"):
        chain += f"  →  {env['LLM_FALLBACKS']}"
    table.add_row("AI chain", chain)
    table.add_row("Student bot", _mask(env.get("TELEGRAM_BOT_TOKEN_STUDENT", "")))
    table.add_row("Tutor bot", _mask(env.get("TELEGRAM_BOT_TOKEN_OWNER", "")))
    table.add_row("Tutor chat ID", env.get("TUTOR_CHAT_ID", ""))
    table.add_row("Knowledge", env.get("OBSIDIAN_VAULT_PATH") or "./data/knowledge (built-in)")
    planerka = "connected" if env.get("PLANERKA_BASE_URL") else "off"
    table.add_row("Planerka", planerka)
    console.print(table)


def _mask(value: str) -> str:
    if not value:
        return "[err]not set[/err]"
    return value[:6] + "…" + value[-4:] if len(value) > 12 else "set"
