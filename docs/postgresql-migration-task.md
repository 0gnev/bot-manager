# PostgreSQL Migration Status

## Current status

The core migration from JSON-file runtime state to PostgreSQL is complete.

Bridge now uses PostgreSQL as the operational source of truth for:

- contacts
- bookings
- messages
- attachments
- escalations
- approvals
- runtime controls
- idempotency keys

Legacy JSON files under `data/state/` still matter for:

- one-shot imports from older environments
- backup/restore during first-rollout migrations
- preserving historical pre-PostgreSQL state when needed

## Completed scope

- [x] Introduce PostgreSQL as the source of truth for operational state.
- [x] Add schema migrations and a repeatable migration runner.
- [x] Replace file-backed repositories for active runtime state with DB-backed repositories.
- [x] Preserve current business behavior while removing the single-escalation and single-booking bottlenecks.
- [x] Add automated tests for repositories, migrations, and critical workflows.
- [x] Update local/dev and production deployment to provision and run PostgreSQL.
- [x] Add a safe migration/import path from existing `data/state/` JSON files.

This migration still does **not** require Redis. Redis may be evaluated later
for cache, TTL/idempotency, or queue optimizations, but PostgreSQL is already
serving as the first production-grade storage layer.

## Remaining follow-ups

- [x] Keep first-rollout backup/import/rollback runbooks explicit for older environments that still carry legacy `data/state/`.
- [x] Harden student-booking linking around exact Planerka Telegram username matching and guarded deeplink fallback.
- [ ] Decide whether separate `contact_channels` and `deliveries` tables are still needed or whether the current denormalized design is sufficient.
- [ ] Add optional follow-up tables such as `conversation_snapshots` and `escalation_events` only if operationally justified.

## Target Data Model

Current implementation status:

- `contacts`
  - implemented
  - canonical student identity across multiple bookings
- `contact_channels`
  - not implemented as a separate table
  - current design stores Telegram username, e-mail, phone, and similar fields directly on `contacts`
- `bookings`
  - implemented
  - stores booking payload, organizer/attendee metadata, status, and meeting info
- `messages`
  - implemented
  - stores one row per inbound/outbound message with direction, booking context, transport ids, content, timestamps, and model output
- `escalations`
  - implemented
  - one row per escalated question with its own `escalation_id`
- `deliveries`
  - not implemented as a separate table
  - current design stores delivery status and transport metadata on messages plus audit logs
- `idempotency_keys`
  - implemented
  - webhook/update deduplication with expiry metadata

Recommended follow-up or optional tables:

- `attachments`
- `conversation_snapshots`
- `escalation_events`

## Functional Requirements

- [x] A student may have multiple active bookings.
- [x] A student may ask many questions in one chat.
- [x] The bot must continue answering safe questions even while some other student
  questions are waiting for the tutor.
- [x] Each unknown question must create an independent escalation record.
- [x] Tutor replies must resolve the correct escalation, not the entire booking.
- [x] Tutor replies must still be preserved in message history and reused in later
  model context.
- [x] Chat automation must not be globally disabled for a booking just because one
  question was escalated.

## Implementation Requirements

### Storage layer

- [x] Add a database module and repository layer instead of direct JSON-file writes
  from handlers.
- [x] Keep file-based audit and runtime logs unless explicitly migrated later.
- [x] Use transactional writes for multi-step flows such as:
  - inbound message persisted
  - escalation persisted
  - tutor card delivery metadata persisted

### Schema and migrations

- [x] Add SQL migrations for the initial schema.
- [x] Add a migration runner invoked in local/dev, CI, and deployment.
- [x] Add an import command that reads current `data/state/` files and inserts them
  into PostgreSQL.

### Runtime behavior

- [x] Replace one-result booking lookup with contact-aware booking resolution.
- [x] Replace single-file escalation storage with multi-record escalation storage.
- [x] Keep backward-compatible tutor reply routing during rollout where practical.

## Testing Requirements

Current automated coverage includes:

- [x] schema migration bootstrap on an empty database
- [x] import from existing JSON state into PostgreSQL
- [x] booking lookup when one student has multiple bookings
- [x] creating multiple open escalations for one booking
- [x] tutor reply resolving the intended escalation
- [x] preserving tutor replies in conversation/message history
- [x] idempotency behavior for webhook and Telegram updates
- [ ] deployment smoke test that bridge starts and can reach PostgreSQL as a dedicated automated workflow

Testing expectations:

- implemented:
  - unit tests for repository methods
  - integration tests against a real PostgreSQL instance in CI
  - workflow tests for student message -> escalation -> tutor reply -> student delivery

## Deployment Requirements

### Local/dev

- [x] `compose.yaml` includes PostgreSQL for local development.
- [x] `.env.example` includes the required database env vars.
- [x] `bridge` startup runs migrations before serving traffic, and `task up` brings the stack up through Docker Compose.

### Production

- [x] Production deployment brings PostgreSQL up through Docker Compose or can point to an external DB via `DATABASE_URL`.
- [x] DB migrations run automatically during `bridge` startup in deployment.
- [x] Back up current `data/state/` before migration/import in any first-rollout legacy environment.
- [x] Import existing runtime state into PostgreSQL on first rollout for any remaining legacy environment.
- [x] Bridge health, Telegram polling, tutor reply routing, and webhook handling are part of the documented post-deploy checks.

## Acceptance Criteria

- [x] Bridge no longer depends on JSON files for operational state reads/writes.
- [x] Existing state can be imported from `data/state/` without losing active bookings, history, or pending escalations.
- [x] One student with multiple bookings is handled deterministically.
- [x] Multiple pending escalations for one booking are supported.
- [x] Tutor replies continue to reach the student and resolve the right escalation.
- [x] CI runs the PostgreSQL-backed test suite successfully.
- [x] Production deployment instructions are updated.
- [x] First-rollout legacy-environment validation should remain explicit in operational runbooks.

## Rollout Plan

1. Keep the current PostgreSQL schema and migration chain as the source of truth.
2. Preserve CI coverage against PostgreSQL on Python 3.12.
3. For any legacy environment, run:

   ```bash
   task pg-first-rollout
   ```

   This does all of the following in order:
   - backs up legacy `data/state/` into `data/backups/postgres-first-rollout/<timestamp>/legacy-state.tar.gz`
   - starts `postgres` and waits for readiness
   - creates a PostgreSQL custom-format dump before any import
   - imports legacy JSON state into PostgreSQL in `auto` mode only when runtime tables are still empty
   - recreates the full stack and waits for `bridge` health
   - writes `manifest.env` with the backup paths needed for rollback

4. Run smoke checks for booking linking, student Q&A, escalation, and tutor reply routing.

Useful rollout switches:

```bash
IMPORT_LEGACY_STATE=always task pg-first-rollout
IMPORT_LEGACY_STATE=never task pg-first-rollout
```

## Rollback Plan

- Keep the JSON state backup until any legacy-environment PostgreSQL rollout is verified.
- If a first-rollout rollout fails, restore from the backup manifest created by `task pg-first-rollout`:

  ```bash
  BACKUP_DIR=data/backups/postgres-first-rollout/<timestamp> task pg-rollback
  ```

  If `BACKUP_DIR` is omitted, the latest backup directory is used.

- The rollback flow:
  - starts `postgres`
  - stops `bridge`
  - restores the PostgreSQL custom dump with `pg_restore --clean --if-exists`
  - restores the legacy `data/state/` archive if one was captured
  - recreates the stack and waits for bridge health

- Do not delete `data/state/` during the first PostgreSQL rollout in environments that still rely on it for import.

## Related Files

- [architecture-execution-plan.md](/private/var/www/bot-manager/docs/architecture-execution-plan.md)
- [project-handoff.md](/private/var/www/bot-manager/docs/project-handoff.md)
- [bookings.py](/private/var/www/bot-manager/app/bridge/state/bookings.py)
- [conversations.py](/private/var/www/bot-manager/app/bridge/state/conversations.py)
- [escalations.py](/private/var/www/bot-manager/app/bridge/state/escalations.py)
- [compose.yaml](/private/var/www/bot-manager/compose.yaml)
