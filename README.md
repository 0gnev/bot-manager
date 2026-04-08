# Bot Manager

Bot Manager is a Telegram-based tutor communication system integrated with
Planerka bookings, OpenClaw AI, and PostgreSQL runtime state.

The system receives booking events from Planerka, links students to contacts,
answers standard questions automatically when safe, escalates ambiguous or risky
cases to the tutor, and lets the tutor continue the dialogue from Telegram.

## What the system does

- accepts Planerka webhook events such as `BOOKING_CREATED`
- keeps runtime state in PostgreSQL
- routes student Telegram messages through a contact-first conversation model
- uses optional booking context when it exists
- answers known questions automatically through OpenClaw
- blocks off-topic / unsafe answers through policy checks
- escalates to the tutor when the model should not answer
- supports tutor review, approval, revision, direct-send, and dialogue export
- supports Telegram-controlled global runtime modes:
  - `/status`
  - `/pause <reason>`
  - `/panic <reason>`
  - `/resume <reason>`

## Current runtime architecture

Core services:

- `bridge` — main FastAPI + Telegram orchestration service
- `openclaw` — AI gateway used by Bridge
- `postgres` — operational state store

Current data model:

- `contacts` own student identity and chat metadata
- `contact_channels` store normalized Telegram/email/phone reachability
- `bookings` are optional context attached to conversations
- `messages` store dialogue history, direction, attachments, raw model output,
  delivery status, and transport IDs
- `deliveries` store transport-level send/receive attempts
- `approvals` and `escalations` are separate operational records
- `runtime_controls` store global automation mode and incident metadata

High-level flow:

1. Planerka sends webhook events into `bridge`
2. `bridge` stores booking/contact state in PostgreSQL
3. student messages enter through the public Telegram bot
4. `bridge` resolves contact + optional booking context
5. `bridge` calls OpenClaw when automation is allowed
6. policy decides whether to send, clarify, approve, or escalate
7. tutor handles escalations and approvals through the tutor Telegram bot

## Repository layout

- `app/` — Python application code
- `config/` — checked-in config and prompt templates
- `data/` — runtime data on local/dev and bind-mounted runtime folders
- `docs/` — detailed technical and operational documentation
- `scripts/` — deploy, migration, cleanup, and helper scripts
- `tests/` — unit, integration, system, and live-LLM tests
- `Taskfile.yml` — common local commands

Runtime folders under `data/`:

- `data/audit/` — audit JSONL
- `data/knowledge/` — mirrored knowledge and exports
- `data/logs/` — rotating bridge logs
- `data/openclaw/` — OpenClaw runtime state/config
- `data/uploads/` — persisted uploaded media
- `data/state/` — legacy JSON state kept only for import/backup compatibility

## Local quick start

1. Create the env file:

```bash
cp .env.example .env
```

2. Prepare local folders:

```bash
task init
```

3. Fill in real secrets in `.env`.

4. Start the local stack:

```bash
task up
```

5. Follow logs:

```bash
task logs
```

If code or config changes are not picked up by an existing `bridge` container:

```bash
docker compose --env-file .env up -d --build --force-recreate bridge
```

## Important environment variables

Use [.env.example](/private/var/www/bot-manager/.env.example) as the schema.

Important variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `TELEGRAM_BOT_TOKEN_STUDENT`
- `TELEGRAM_BOT_TOKEN_OWNER`
- `TUTOR_CHAT_ID`
- `PLANERKA_BASE_URL`
- `PLANERKA_API_KEY`
- `PLANERKA_WEBHOOK_SECRET`
- `GATEWAY_AUTH_TOKEN`
- `TUTOR_API_TOKEN`
- `DATABASE_URL`
- `OBSIDIAN_SOURCE_PATH`
- `KNOWLEDGE_MIRROR_PATH`
- `AUDIT_PATH`

Operational meaning:

- `TELEGRAM_BOT_TOKEN_STUDENT` is the public student-facing bot
- `TELEGRAM_BOT_TOKEN_OWNER` is the tutor/operator bot
- `TUTOR_CHAT_ID` is the allowlisted tutor chat
- `DATABASE_URL` is the production runtime source of truth
- `OPENCLAW_CONFIG_PATH` must point to the intended OpenClaw config path inside
  the container runtime

## Common local commands

Task-based workflow:

```bash
task init
task up
task down
task restart
task logs
task ps
task test
task test-system
task tunnel
task tunnel-url
task pg-first-rollout
task pg-rollback
```

What these do:

- `task up` — start local stack
- `task down` — stop local stack
- `task restart` — restart local stack
- `task logs` — tail service logs
- `task ps` — show container status
- `task test` — full Docker-backed `pytest`
- `task test-system` — system tests only
- `task tunnel` — start `bridge` plus Cloudflare tunnel for webhook testing
- `task tunnel-url` — print current tunnel URL
- `task pg-first-rollout` — backup/import flow for old JSON-state environments
- `task pg-rollback` — restore PostgreSQL + legacy state from rollout backup

## Testing

Deterministic test layers:

- unit/integration tests through `task test`
- system tests through `task test-system`

Live provider checks:

- `Live LLM Smoke` GitHub Actions workflow
- optional local smoke path against a real OpenClaw container

Important testing rule in this repo:

- normal tests use an isolated test database
- docs-only changes do not require a test run

Detailed testing docs:

- [docs/system-testing.md](/private/var/www/bot-manager/docs/system-testing.md)

## Tutor-side operational commands

Tutor Telegram commands currently include:

- `/mode <booking_id> auto|semi-auto|manual`
- `/dialog <username|email|name|booking_id>`
- `/timezone <tz>`
- `/status`
- `/pause <reason>`
- `/panic <reason>`
- `/resume <reason>`

Behavioral notes:

- plain reply to an approval card means “revise the draft”
- `/send ...` in reply to an approval card means “send this exact text now”
- `Отправить студенту` sends the current draft as-is

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

Why `--force-recreate` matters:

- `bridge` uses bind mounts for app/config/scripts
- a plain `up -d --build` can leave the old Python process running
- `--force-recreate` guarantees the new code is actually serving traffic

Detailed deploy docs:

- [docs/github-actions.md](/private/var/www/bot-manager/docs/github-actions.md)

## Temporary shutdown to save server cost

If you want to stop the production server for a while without deleting its disk:

1. In the tutor bot:

```text
/panic planned_maintenance
```

2. On the server:

```bash
cd /opt/bot-manager
docker compose --env-file .env down
```

3. Power off the VM in the cloud provider.

When you start the same machine again:

```bash
cd /opt/bot-manager
docker compose --env-file .env up -d --build --force-recreate
curl -fsS http://127.0.0.1:8081/health
```

Then clear panic mode in the tutor bot:

```text
/resume maintenance_complete
```

If the server will be deleted entirely, use the full backup procedure from:

- [docs/production-operations.md](/private/var/www/bot-manager/docs/production-operations.md)

## Important operational constraints

- OpenClaw Telegram channel must stay disabled; Bridge owns Telegram polling
- `config/openclaw.json` must stay strict JSON; do not add pseudo-comment keys
- tracked runtime state under `data/` is intentionally minimized to avoid deploy drift
- student-booking linking currently relies on exact Planerka Telegram username
  matching plus existing `telegram_user_id` bindings

## Documentation map

- local development: [docs/local-dev.md](/private/var/www/bot-manager/docs/local-dev.md)
- local and remote test workflow: [docs/system-testing.md](/private/var/www/bot-manager/docs/system-testing.md)
- GitHub Actions / deploy: [docs/github-actions.md](/private/var/www/bot-manager/docs/github-actions.md)
- PostgreSQL migration / first-rollout backup+rollback: [docs/postgresql-migration-task.md](/private/var/www/bot-manager/docs/postgresql-migration-task.md)
- production shutdown / restart / restore runbook: [docs/production-operations.md](/private/var/www/bot-manager/docs/production-operations.md)
- operational handoff: [docs/project-handoff.md](/private/var/www/bot-manager/docs/project-handoff.md)
