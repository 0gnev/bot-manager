# Bot Manager Project Handoff

This document is the practical backup context for moving the project to a new PC
or re-establishing operational context after a break.

It is intentionally focused on how the project works today, how to run it, how
to deploy it, and what operational traps are easy to forget.

## 1. Project purpose

Bot Manager is a managed post-booking communication system for tutor operations
in Telegram.

The system starts working after a student books through Planerka.

Main responsibilities:

- receive booking events from Planerka
- start and maintain student conversations
- answer safe/standard questions automatically
- escalate risky or non-standard cases to the tutor
- allow the tutor to answer students through Telegram
- use Obsidian-maintained knowledge without hardcoding all behavior in code

The intended architecture is:

- Planerka
- Telegram transport
- Bot Manager orchestration layer
- OpenClaw / AI layer
- Obsidian knowledge layer
- human tutor as final authority

## 2. Current architecture in the repo

### Core runtime services

- `bridge`: the main Bot Manager service
- `openclaw`: AI gateway used by Bridge
- `postgres`: operational state store used by Bridge at runtime

### Conversation ownership model

Current runtime behavior is contact-first:

- a Telegram student is represented by `contacts`
- normalized reachability data is stored in `contact_channels`
- chat metadata lives on `contacts` when a contact exists
- `bookings` are optional context attached to messages, escalations, and approvals
- students can talk to the bot without an existing booking
- tutor controls are available both for booking-scoped chats and directly for contact-scoped chats
- stored message records persist direction, delivery status, transport IDs,
  attachment metadata, and raw model output when available
- transport-level delivery attempts are also stored in `deliveries`

Why this matters:

- do not assume every inbound student message has a valid `booking_id`
- contact-only questions can still create escalations and approvals
- API and state changes must preserve both `contact_id` and optional `booking_id`

### Telegram ownership model

Current intended routing:

- student bot is polled by `bridge`
- tutor/owner bot is also polled by `bridge`
- OpenClaw Telegram channel must stay disabled

Why this matters:

- tutor `reply` to a student escalation card must be handled by Bot Manager
- if OpenClaw also polls Telegram, reply routing breaks or conflicts with Bridge
- escalation cards now include a deterministic summary, recent dialogue excerpt,
  and a draft reply when policy logic escalates an otherwise answerable message
- approval cards now include inline approve/reject buttons
- plain tutor reply to an approval card means “revise the draft and show me the next version”
- tutor must use `/send ...` in a reply to an approval card when exact text should go to the student immediately
- REST flow follows the same split:
  - `POST /api/approvals/{id}/approve` sends the current draft as-is
  - `POST /api/approvals/{id}/revise` rewrites the draft but keeps it pending
  - `POST /api/approvals/{id}/edit-approve` sends explicit tutor text immediately

Relevant files:

- [compose.yaml](/private/var/www/bot-manager/compose.yaml)
- [config/botmanager.json](/private/var/www/bot-manager/config/botmanager.json)
- [config/routing.json](/private/var/www/bot-manager/config/routing.json)
- [config/openclaw.json](/private/var/www/bot-manager/config/openclaw.json)
- [app/bridge/api/tutor.py](/private/var/www/bot-manager/app/bridge/api/tutor.py)

Tutor/operator review surface now exists in the API:

- `GET /api/tutor/chats` for recent contact-scoped dialogue summaries
- `GET /api/tutor/chats/{booking_id}` for booking-scoped message review
- `GET /api/tutor/contacts/{contact_id}/chat` for contact-scoped message review
- tutor Telegram flow can now export a full dialogue transcript directly into the tutor chat by username, e-mail, name, or explicit `booking_id`
- explicit Telegram command: `/dialog username|email|имя|booking_id`

Important config constraint:

- `config/openclaw.json` must stay strict JSON
- do not add pseudo-comment keys such as `$comment`
- recent OpenClaw builds reject unknown root keys during startup

## 3. Environment variables you must preserve

Use `.env.example` as the reference schema.

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

Reference:

- [.env.example](/private/var/www/bot-manager/.env.example)

Important operational meaning:

- `TELEGRAM_BOT_TOKEN_STUDENT` is the public student-facing bot
- `TELEGRAM_BOT_TOKEN_OWNER` is the tutor/operator bot
- `TUTOR_CHAT_ID` is the Telegram allowlist entry for the tutor bot

## 4. Secrets and non-repo assets to back up

Do not rely on the repo alone. These must be backed up separately:

- the real `.env`
- GitHub deploy SSH private key used by Actions
- server `known_hosts` entry
- GitHub Actions repository secrets
- local SSH config used for GitHub access from the server
- any real Obsidian vault outside the repo

Recommended private backup checklist:

- copy the production `.env`
- copy `~/.ssh/config`
- copy the private key used for GitHub deploy access
- copy the server host fingerprint / `known_hosts` entry
- export the GitHub Actions secrets list and values to a secure password manager

## 5. Production deployment model

### Current deployment flow

GitHub Actions CD SSHes into the server and runs:

```bash
bash scripts/deploy-server.sh main
```

That script:

1. marks the repo path as a safe Git directory
2. fetches refs and checks out the requested branch
3. pulls `main`
4. runs `docker compose --env-file .env up -d --build --force-recreate`
5. starts `postgres`, `openclaw`, and `bridge` with the new code/config
6. lets `bridge` apply any pending PostgreSQL migrations during startup
7. waits for `bridge` to become healthy on `http://127.0.0.1:8081/health`
8. prints recent `bridge` logs and fails if startup never becomes healthy

Relevant files:

- [scripts/deploy-server.sh](/private/var/www/bot-manager/scripts/deploy-server.sh)
- [.github/workflows/cd.yml](/private/var/www/bot-manager/.github/workflows/cd.yml)
- [docs/github-actions.md](/private/var/www/bot-manager/docs/github-actions.md)

`pytest` remains part of `CI`, not `CD`. This matters after the PostgreSQL
migration because the state-backed tests require a dedicated test database.
Running them directly on the production server caused deploy failures when no
local PostgreSQL test instance was available.

### Why `--force-recreate` is required

`bridge` uses bind mounts for:

- `app/`
- `scripts/`
- `config/`

A plain `docker compose up -d --build` can leave the existing `bridge`
container running, which means the old Python process may continue serving
requests even if the image was rebuilt.

`--force-recreate` is required to guarantee that the new code is actually
running.

## 6. Local bootstrap on a new machine

### Minimal setup

```bash
git clone <repo-url>
cd bot-manager
bash scripts/init-local.sh
cp .env.example .env
# fill in real secrets
docker compose --env-file .env up -d --build --force-recreate
docker compose --env-file .env logs -f --tail=200
```

Task runner shortcuts:

```bash
task init
task up
task logs
```

References:

- [README.md](/private/var/www/bot-manager/README.md)
- [docs/local-dev.md](/private/var/www/bot-manager/docs/local-dev.md)
- [Taskfile.yml](/private/var/www/bot-manager/Taskfile.yml)

## 7. Production server assumptions

These values are operationally important and should be remembered or stored in a
private note:

- repo path on server: `/opt/bot-manager`
- deploy is usually run as user `deploy`
- GitHub access from the server uses SSH

Common server Git fixes:

```bash
git config --global --add safe.directory /opt/bot-manager
```

If the server cannot pull from GitHub, verify:

- `~/.ssh/config`
- the correct private key path
- `ssh -T git@github.com`

## 8. Telegram runtime rules you must not forget

### Intended bot routing

- `bridge` polls the student bot
- `bridge` polls the tutor/owner bot
- OpenClaw Telegram channel must remain disabled

### Critical gotcha

OpenClaw has two configuration layers:

- repo config: `config/openclaw.json`
- persisted runtime config: `data/openclaw/config/openclaw.json`
- runtime env path: `OPENCLAW_CONFIG_PATH` (defaults to `/workspace/config/openclaw.json`)

Even if the repo config has Telegram disabled, the persisted OpenClaw runtime
config may still have Telegram enabled.

The Docker service must mount `./config` into `/workspace/config`, otherwise
OpenClaw can start without the intended repo config and fall back to its own
internal defaults.

If that happens, OpenClaw will start its own Telegram provider and cause polling
conflicts with Bridge.

### Required state

Both of these should effectively have Telegram disabled for OpenClaw:

- [config/openclaw.json](/private/var/www/bot-manager/config/openclaw.json)
- `data/openclaw/config/openclaw.json` on the deployed machine

### Symptom of bad state

If Telegram is enabled in OpenClaw, logs will show `409 Conflict` or
`getUpdates conflict`.

That means more than one process is polling the same bot token.

## 9. How tutor replies are supposed to work

### Intended behavior

- student sends a message
- Bridge decides whether to answer automatically or escalate
- if escalation is needed, tutor receives a Telegram card
- tutor replies to that card using Telegram `reply`
- Bridge routes the tutor text back to the student

### Important distinction

- tutor message with `reply` on a student escalation card = operator action to a student
- tutor message without `reply` = internal tutor-assistant usage

### Current known limitation

Escalations are now stored as separate PostgreSQL records with their own
`escalation_id`, and tutor/API replies can resolve the exact record.

The remaining operational limitation is narrower:

- booking-level reply flows are only unambiguous when there is exactly one
  pending escalation for that booking
- if multiple pending escalations exist and the exact tutor card / message link
  is lost, the operator must reply to the correct card or use the explicit
  `escalation_id`
- legacy fallback flows that infer context from `booking_id` are still kept for
  backward compatibility during rollout

## 10. State, database, and data directories

The main operational source of truth is now PostgreSQL.

`data/` still matters, but not all folders there are equal:

- `postgres` Docker volume / external PostgreSQL host
  - this is where operational state lives at runtime:
    contacts, contact_channels, bookings, messages, attachments, approvals,
    escalations, deliveries, runtime_controls, idempotency_keys
- `data/audit`
  - audit trail is stored in `data/audit/audit.jsonl`
- `data/logs`
  - runtime bridge logs are stored in `data/logs/bridge.log` with rotation
- `data/knowledge`
  - mirrored knowledge base and Obsidian-compatible exports
- `data/uploads`
  - persisted uploaded media used in student/tutor message flows
- `data/openclaw`
  - OpenClaw runtime state and persisted config
- `data/state`
  - legacy JSON state kept for migration/import, backup, or older environment
    restores; it is no longer the primary runtime storage layer

If you move environments or restore from backup, back up both the PostgreSQL
data and the operational folders under `data/`.

### First-rollout legacy state automation

For environments that still carry old JSON state under `data/state`, use:

```bash
task pg-first-rollout
```

This creates a timestamped directory under:

- `data/backups/postgres-first-rollout/<timestamp>`

Inside that directory:

- `legacy-state.tar.gz`
  - backup of `data/state` if legacy JSON files still exist
- `bridge-pre-rollout.dump`
  - PostgreSQL custom-format dump from before any import
- `manifest.env`
  - recorded paths and rollout mode for rollback

If you need to force or skip the import step:

```bash
IMPORT_LEGACY_STATE=always task pg-first-rollout
IMPORT_LEGACY_STATE=never task pg-first-rollout
```

Rollback uses the saved manifest:

```bash
BACKUP_DIR=data/backups/postgres-first-rollout/<timestamp> task pg-rollback
```

If `BACKUP_DIR` is omitted, the latest first-rollout backup directory is used.

## 11. Fast operational checks after deployment

### Health check

```bash
docker compose --env-file .env ps
docker compose --env-file .env logs -f --since=2m bridge
```

Expected:

- `postgres` healthy
- `bridge` healthy
- `openclaw` healthy
- Bridge logs show polling for both bots
- no Telegram conflict errors

### Student-to-tutor escalation check

1. Send a new student question that should be escalated.
2. Confirm the tutor receives a Telegram card.
3. Reply to that card from the tutor bot.
4. Confirm the student receives the tutor's answer.

### Runtime log access

Bridge runtime logs can be read in three ways:

- `docker compose --env-file .env logs -f bridge`
- `tail -f data/logs/bridge.log`
- authenticated API: `GET /api/logs/runtime`

### Files to inspect during debugging

```bash
docker compose --env-file .env exec -T postgres psql -U bridge -d bridge -c \
  "SELECT id, booking_id, contact_id, status, tutor_message_id, created_at FROM escalations ORDER BY created_at DESC LIMIT 20;"
grep -RIn "<booking_id>" data/audit | tail -n 30
docker compose --env-file .env logs --since=2m bridge
```

## 12. Key debugging patterns

### If tutor reply does not reach the student

Check:

- was the reply sent to the correct escalation card?
- if there are multiple pending escalations, was the exact card / `escalation_id`
  used?
- do the booking or contact rows contain `telegram_user_id`?
- do Bridge logs show tutor-reply routing logs?

### If deploy succeeds but behavior does not change

Most likely causes:

- container was not recreated
- server is still running an older process
- OpenClaw persisted runtime config overrides repo config

Preferred fix:

```bash
docker compose --env-file .env up -d --build --force-recreate bridge
```

### If Git pull fails on the server

Common causes:

- `dubious ownership`
- wrong SSH key
- missing `known_hosts`
- local dirty file under `data/`

## 13. Tests and verification

Current tests cover:

- delivery
- idempotency
- policy engine
- state workflows
- tutor API behavior
- OpenClaw client behavior
- import from legacy JSON state
- end-to-end system message flow in the Docker harness
- live LLM smoke workflow for real-provider OpenClaw validation

Runtime resiliency now includes bounded retries:

- outbound student-facing Telegram delivery retries before marking delivery failed
- OpenClaw gateway calls retry on transport failures and retryable HTTP statuses

Preferred local run path:

```bash
task test
```

Direct equivalent:

```bash
bash scripts/run-pytest-in-docker.sh
```

Why this matters:

- the intended local test environment is Python 3.12 inside Docker
- CI uses Python 3.12 with a PostgreSQL service
- relying on the host Python interpreter is no longer recommended unless it
  matches the intended runtime/test environment

Relevant test files:

- [test_delivery.py](/private/var/www/bot-manager/tests/test_delivery.py)
- [test_idempotency.py](/private/var/www/bot-manager/tests/test_idempotency.py)
- [test_policy_engine.py](/private/var/www/bot-manager/tests/test_policy_engine.py)
- [test_state_workflows.py](/private/var/www/bot-manager/tests/test_state_workflows.py)
- [test_api_tutor.py](/private/var/www/bot-manager/tests/test_api_tutor.py)
- [test_openclaw_client.py](/private/var/www/bot-manager/tests/test_openclaw_client.py)
- [test_openclaw_live.py](/private/var/www/bot-manager/tests/live/test_openclaw_live.py)

## 14. Known architectural gaps

These are not forgotten bugs; they are still-open follow-up tasks.

- booking linking now relies on exact Planerka Telegram username matching plus existing telegram_user_id bindings; signed claim/deeplink tokens are intentionally deferred until there is a reliable delivery channel
- emergency-stop/admin surface can be stronger
- optional event/audit-style tables such as `conversation_snapshots` or `escalation_events` can still be added later if operations actually need them

Reference:

- [architecture-execution-plan.md](/private/var/www/bot-manager/docs/architecture-execution-plan.md)
- [postgresql-migration-task.md](/private/var/www/bot-manager/docs/postgresql-migration-task.md)

## 15. What to keep in a private personal backup

Keep a secure note outside Git with:

- current production host/IP
- server SSH user
- repo path on server
- `.env` contents
- GitHub deploy SSH key location
- GitHub Actions secret values
- tutor Telegram chat ID
- Planerka credentials
- Obsidian vault path

This document is the project memory.
