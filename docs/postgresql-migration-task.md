# PostgreSQL Migration Task

## Goal

Replace the current JSON-file state storage with PostgreSQL-backed persistence
that supports:

- many students in parallel
- multiple active bookings per student
- multiple open escalations per booking
- durable message history and reply routing
- safe production deployment with backup, migration, and rollback steps

This task should also introduce the automated test coverage and deployment
changes required to operate the new storage model in production.

## Why This Task Exists

The current architecture stores state in local JSON files under `data/state/`.
That implementation is good enough for a bootstrap phase, but it has hard
limits:

- booking lookup is effectively one-result-per-Telegram-user
- escalation state is one-record-per-booking instead of many records
- message history is too thin for reliable workflow reconstruction
- file-backed state makes multi-step updates and concurrent workflows brittle

## Scope

This task includes:

1. Introduce PostgreSQL as the source of truth for operational state.
2. Add schema migrations and a repeatable migration runner.
3. Replace file-backed repositories for active runtime state with DB-backed
   repositories.
4. Preserve current business behavior while removing the single-escalation and
   single-booking bottlenecks.
5. Add automated tests for repositories, migrations, and critical workflows.
6. Update local/dev and production deployment to provision and run PostgreSQL.
7. Add a safe migration/import path from existing `data/state/` JSON files.

This task does **not** require Redis. Redis may be evaluated later for cache,
TTL/idempotency, or queue optimizations, but PostgreSQL should be sufficient for
the first production-grade storage layer.

## Target Data Model

At minimum, introduce tables or equivalent models for:

- `contacts`
  - canonical student identity across multiple bookings
  - Telegram user id and other stable contact fields
- `contact_channels`
  - telegram username, email, phone, etc.
- `bookings`
  - booking payload, organizer/attendee metadata, status, meeting info
- `messages`
  - one row per inbound/outbound message
  - direction, sender type, booking context, transport ids, content, timestamps
- `escalations`
  - one row per escalated question with its own `escalation_id`
  - status, reason, source message id, tutor message id, tutor reply
- `deliveries`
  - outbound delivery attempts and delivery status
- `idempotency_keys`
  - webhook/update deduplication with TTL or expiry metadata

Recommended follow-up or optional tables:

- `attachments`
- `conversation_snapshots`
- `escalation_events`

## Functional Requirements

- A student may have multiple active bookings.
- A student may ask many questions in one chat.
- The bot must continue answering safe questions even while some other student
  questions are waiting for the tutor.
- Each unknown question must create an independent escalation record.
- Tutor replies must resolve the correct escalation, not the entire booking.
- Tutor replies must still be preserved in message history and reused in later
  model context.
- Chat automation must not be globally disabled for a booking just because one
  question was escalated.

## Implementation Requirements

### Storage layer

- Add a database module and repository layer instead of direct JSON-file writes
  from handlers.
- Keep file-based audit and runtime logs unless explicitly migrated later.
- Use transactional writes for multi-step flows such as:
  - inbound message persisted
  - escalation persisted
  - tutor card delivery metadata persisted

### Schema and migrations

- Add SQL migrations for the initial schema.
- Add a migration runner invoked in local/dev, CI, and deployment.
- Add an import command that reads current `data/state/` files and inserts them
  into PostgreSQL.

### Runtime behavior

- Replace one-result booking lookup with contact-aware booking resolution.
- Replace single-file escalation storage with multi-record escalation storage.
- Keep backward-compatible tutor reply routing during rollout where practical.

## Testing Requirements

Add or update automated tests for:

- schema migration bootstrap on an empty database
- import from existing JSON state into PostgreSQL
- booking lookup when one student has multiple bookings
- creating multiple open escalations for one booking
- tutor reply resolving the intended escalation
- preserving tutor replies in conversation/message history
- idempotency behavior for webhook and Telegram updates
- deployment smoke test that bridge starts and can reach PostgreSQL

Testing expectations:

- unit tests for repository methods
- integration tests against a real PostgreSQL instance in CI
- workflow tests for student message -> escalation -> tutor reply -> student
  delivery

## Deployment Requirements

### Local/dev

- Extend `compose.yaml` with PostgreSQL for local development.
- Add the required env vars to `.env.example`.
- Ensure `task up` or equivalent startup path brings the database up and runs
  migrations before bridge starts serving traffic.

### Production

- Update deployment so PostgreSQL is available on the server, either:
  - as a new Docker Compose service, or
  - as an external managed/Postgres host configured via env vars
- Run DB migrations during deployment before recreating the bridge container.
- Back up current `data/state/` before migration/import.
- Import existing runtime state into PostgreSQL on first rollout.
- Verify bridge health, Telegram polling, tutor reply routing, and webhook
  handling after deploy.

## Acceptance Criteria

- Bridge no longer depends on JSON files for operational state reads/writes.
- Existing state can be imported from `data/state/` without losing active
  bookings, history, or pending escalations.
- One student with multiple bookings is handled deterministically.
- Multiple pending escalations for one booking are supported.
- Tutor replies continue to reach the student and resolve the right escalation.
- CI runs the PostgreSQL-backed test suite successfully.
- Production deployment instructions are updated and validated.

## Rollout Plan

1. Add schema, repositories, and migration runner.
2. Add PostgreSQL-backed tests in CI.
3. Add import script from JSON state.
4. Deploy PostgreSQL and run migrations on the server.
5. Back up `data/state/`.
6. Import current state into PostgreSQL.
7. Deploy bridge with PostgreSQL enabled.
8. Run smoke checks for booking linking, student Q&A, escalation, and tutor
   reply routing.

## Rollback Plan

- Keep the JSON state backup until PostgreSQL rollout is verified.
- If rollout fails, stop the new bridge version, restore the previous bridge
  image/config, and switch back to the last known-good JSON-backed release.
- Do not delete `data/state/` during the first PostgreSQL rollout.

## Related Files

- [architecture-execution-plan.md](/private/var/www/bot-manager/docs/architecture-execution-plan.md)
- [project-handoff.md](/private/var/www/bot-manager/docs/project-handoff.md)
- [bookings.py](/private/var/www/bot-manager/app/bridge/state/bookings.py)
- [conversations.py](/private/var/www/bot-manager/app/bridge/state/conversations.py)
- [escalations.py](/private/var/www/bot-manager/app/bridge/state/escalations.py)
- [compose.yaml](/private/var/www/bot-manager/compose.yaml)
