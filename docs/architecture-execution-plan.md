# Architecture Execution Plan

Source of truth: the compact architecture prompt provided for Bot Manager.

## Completed in this pass

- [x] Prevent tutor API replies from resolving escalations before student delivery succeeds.
- [x] Treat invalid/non-JSON OpenClaw output as escalation fallback instead of a normal answer.
- [x] Make webhook and Telegram update idempotency atomic for in-flight duplicate deliveries.
- [x] Normalize image-message history so uncaptioned images are still recorded and current image context is not duplicated.
- [x] Add focused regression tests for tutor delivery failure, invalid model output, and in-flight idempotency behavior.
- [x] Move conversation routing to a contact-first model with optional booking context.
- [x] Support contact-only escalations, approvals, and Obsidian exports without synthetic `None-*` identities.
- [x] Add contact-scoped tutor control endpoints for mode and automation management.

## Next priority tasks

- [ ] Implement PostgreSQL-backed runtime state with migration, tests, and production deployment plan.
  Reference: [postgresql-migration-task.md](/private/var/www/bot-manager/docs/postgresql-migration-task.md)
- [x] Add a dedicated `contacts` entity aligned with the architecture instead of keeping contact data only inside booking payloads.
- [x] Expand stored message records to include direction, attachments, model output, delivery status, and transport IDs.
- [x] Enrich escalation packages with summary, relevant conversation history, and draft reply when available.
- [x] Add tutor-facing dialogue review/list endpoints so conversations can be reviewed without reading raw state files.
- [ ] Tighten student-booking linking to a stronger deeplink or signed-token flow instead of username fallback.
- [ ] Add retry/backoff policy around outbound Telegram delivery and OpenClaw calls.
- [ ] Add an explicit emergency-stop/admin control surface beyond raw REST toggles.
- [ ] Run the automated test suite in the intended Python 3.12 environment and wire it into the normal dev workflow.
- [ ] Update `uvicorn`/`websockets` to versions that no longer rely on the deprecated legacy websocket API, then remove the resulting system-test warnings.
