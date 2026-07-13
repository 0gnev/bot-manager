# Bot Manager

A Telegram tutor-communication bot that answers routine student questions
itself, escalates anything risky to the tutor, and keeps all of its knowledge
in plain markdown — readable by you in Obsidian and by the model at answer
time.

Version 2 talks to AI models **directly** — no gateway in between. Pick a
popular cloud model by API key (OpenAI, Anthropic, OpenRouter), your own
local model (Ollama, LM Studio, any OpenAI-compatible server), or both with
automatic failover.

## Install

```bash
git clone <this-repo> bot-manager
cd bot-manager
./install.sh     # installs the `botmgr` command, checks Docker
botmgr setup     # interactive terminal wizard
botmgr doctor    # verifies tokens, AI provider, vault, Docker
botmgr up        # builds and starts the bot
```

The `botmgr` terminal app is the only interface you need day to day:

| Command          | What it does                                      |
| ---------------- | ------------------------------------------------- |
| `botmgr setup`   | guided configuration (Telegram, AI, Obsidian, …)  |
| `botmgr doctor`  | checks every dependency and credential end to end |
| `botmgr up`      | build + start the whole stack in Docker           |
| `botmgr down`    | stop the stack                                    |
| `botmgr restart` | rebuild and restart                               |
| `botmgr status`  | container status + bridge health                  |
| `botmgr logs`    | follow logs (`--tail N`, `--no-follow`)           |

## What the system does

- accepts Planerka webhook events such as `BOOKING_CREATED` (optional)
- keeps runtime state in PostgreSQL
- routes student Telegram messages through a contact-first conversation model
- answers known questions automatically via the configured AI model
- blocks off-topic / unsafe answers through policy checks
- escalates to the tutor when the model should not answer
- supports tutor review, approval, revision, direct-send, and dialogue export
- proposes reusable knowledge notes from tutor-approved answers, saving them
  only after explicit tutor approval
- supports Telegram-controlled global runtime modes:
  `/status`, `/pause <reason>`, `/panic <reason>`, `/resume <reason>`

## AI providers

Configured by `botmgr setup` (or by hand in `.env`):

| Provider     | Type  | Needs                              |
| ------------ | ----- | ---------------------------------- |
| `openai`     | cloud | `OPENAI_API_KEY`                   |
| `anthropic`  | cloud | `ANTHROPIC_API_KEY`                |
| `openrouter` | cloud | `OPENROUTER_API_KEY`               |
| `ollama`     | local | Ollama running on this machine     |
| `lmstudio`   | local | LM Studio local server running     |
| `custom`     | any   | `LLM_BASE_URL` (OpenAI-compatible) |

`LLM_FALLBACKS` adds a failover chain — for example a cloud primary with a
local fallback:

```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-5-mini
LLM_FALLBACKS=ollama:llama3.1:8b
```

If the primary provider is down or erroring, the bridge retries and then
fails over down the chain; if everything fails it escalates the conversation
to the tutor instead of guessing.

## Knowledge base (Obsidian)

Set `OBSIDIAN_VAULT_PATH` to any folder of markdown notes — a dedicated
subfolder of your Obsidian vault works best. The folder is mounted straight
into the container:

- the bot **reads** every `*.md` note as answering context
- the bot **writes** markdown with YAML frontmatter back into the vault:
  - `bookings/` — one card per booking
  - `conversations/` — full dialogue logs
  - `escalations/` — escalation records
  - `approved/` — knowledge notes distilled from tutor-approved answers

Everything is plain text: human-readable in Obsidian, diffable in git,
directly usable as LLM context. There is no sync step — edits you make in
Obsidian are visible to the bot immediately.

## Runtime architecture

Core services (Docker Compose):

- `bridge` — FastAPI + Telegram orchestration service; calls the LLM provider
  chain directly (`app/bridge/llm/`)
- `postgres` — operational state store

High-level flow:

1. Planerka sends webhook events into `bridge` (optional)
2. `bridge` stores booking/contact state in PostgreSQL
3. student messages enter through the public Telegram bot
4. `bridge` resolves contact + optional booking context
5. `bridge` calls the LLM provider chain when automation is allowed
6. policy decides whether to send, clarify, approve, or escalate
7. tutor handles escalations and approvals through the tutor Telegram bot

Data model: `contacts`, `contact_channels`, `bookings`, `messages`,
`deliveries`, `approvals`, `escalations`, `runtime_controls`
(see `app/bridge/state/`).

## Repository layout

- `app/bridge/` — application code (bot, API, policies, LLM layer)
- `app/botmgr/` — the `botmgr` terminal app
- `app/obsidian_adapter/` — markdown vault reader/writer
- `config/` — prompts (`config/prompts/`) and policies (`config/policies/`)
- `data/` — runtime folders (uploads, audit, logs, built-in knowledge)
- `docs/` — detailed technical and operational documentation
- `scripts/` — deploy, migration, and helper scripts
- `tests/` — unit, system, and live-LLM tests

## Tutor-side operational commands

- `/mode <booking_id> auto|semi-auto|manual`
- `/dialog <username|email|name|booking_id>`
- `/timezone <tz>`
- `/status`, `/pause <reason>`, `/panic <reason>`, `/resume <reason>`

Behavioral notes:

- plain reply to an approval card means “revise the draft”
- `/send ...` in reply to an approval card means “send this exact text now”
- `Отправить студенту` sends the current draft as-is
- after a tutor-approved answer, the bot may suggest a reusable knowledge
  note; it is never auto-saved — the tutor must explicitly confirm it

## Development

```bash
pip install -r requirements-dev.txt   # Python 3.11+
PYTHONPATH=app python -m pytest -q tests/ --ignore=tests/system --ignore=tests/live
PYTHONPATH=app python -m pytest -q tests/system     # needs PostgreSQL on :5432
```

Live-LLM smoke tests (real provider, opt-in):

```bash
RUN_LIVE_LLM_TESTS=1 LLM_PROVIDER=openai OPENAI_API_KEY=… \
  PYTHONPATH=app python -m pytest -q -m live_llm tests/live
```

`Taskfile.yml` keeps the `task up / task logs / task test` shortcuts for
development; `botmgr` is the operator-facing interface.

## Production deploy

GitHub Actions `CD` deploys `main` by SSHing into the server and running:

```bash
cd /opt/bot-manager
bash scripts/deploy-server.sh main
```

Manual equivalent:

```bash
cd /opt/bot-manager
git pull --ff-only origin main
docker compose --env-file .env up -d --build --force-recreate
docker compose --env-file .env ps
curl -fsS http://127.0.0.1:8081/health
```

`--force-recreate` matters because `bridge` uses bind mounts — a plain
`up -d --build` can leave the old Python process running.

## Temporary shutdown to save server cost

1. In the tutor bot: `/panic planned_maintenance`
2. On the server: `docker compose --env-file .env down`
3. Power off the VM.

To start again: `docker compose --env-file .env up -d --build
--force-recreate`, check `/health`, then `/resume maintenance_complete` in the
tutor bot. Full backup/restore procedure:
[docs/production-operations.md](docs/production-operations.md).

## Important environment variables

Use [.env.example](.env.example) as the schema — `botmgr setup` fills it in
for you. Key variables: `LLM_PROVIDER`, `LLM_MODEL`, `LLM_FALLBACKS`, the
provider API keys, `TELEGRAM_BOT_TOKEN_STUDENT`, `TELEGRAM_BOT_TOKEN_OWNER`,
`TUTOR_CHAT_ID`, `OBSIDIAN_VAULT_PATH`, and optionally the `PLANERKA_*`
group.

## Documentation map

- local development: [docs/local-dev.md](docs/local-dev.md)
- local and remote test workflow: [docs/system-testing.md](docs/system-testing.md)
- GitHub Actions / deploy: [docs/github-actions.md](docs/github-actions.md)
- PostgreSQL migration / backup+rollback: [docs/postgresql-migration-task.md](docs/postgresql-migration-task.md)
- production shutdown / restore runbook: [docs/production-operations.md](docs/production-operations.md)
- operational handoff: [docs/project-handoff.md](docs/project-handoff.md)
