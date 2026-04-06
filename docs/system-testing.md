# System Testing

This project now has end-to-end system tests for the full student/tutor flow.

The tests run the real `bridge` process and replace external integrations with
controlled fakes:

- fake Planerka webhook input through `POST /webhook/planerka`
- fake Telegram Bot API for both student and tutor bots
- fake OpenClaw API with deterministic responses
- real PostgreSQL state and migrations

## What is covered

The main happy-path system scenario covers:

1. Planerka sends `BOOKING_CREATED`
2. Student opens the bot and sends `/start {booking_id}`
3. Student asks a standard question and gets an automatic answer
4. Student asks a non-standard question and it is escalated to the tutor bot
5. Tutor replies in Telegram and the student receives the reply

Current edge cases covered:

- duplicate Planerka webhook is ignored
- stop-trigger phrases escalate even if the model response is high-confidence
- student without booking stays in contact-only flow
- one student with multiple active bookings must pick the deeplinked booking
- multiple pending escalations for one booking can coexist and be replied to independently
- tutor reply is routed for contact-only escalation without a booking

Test file:

- [tests/system/test_conversation_flow.py](/private/var/www/bot-manager/tests/system/test_conversation_flow.py)

Harness:

- [tests/system/harness.py](/private/var/www/bot-manager/tests/system/harness.py)

## Local usage

Run the full test suite in Docker:

```bash
task test
```

Run only system tests in Docker:

```bash
task test-system
```

These commands do not require local `pytest` installation. They:

- start `postgres` through `docker compose`
- wait for PostgreSQL readiness
- create/use an isolated `bridge_test` database inside that PostgreSQL container
- run tests in a disposable `python:3.12-slim` container
- mount the current repo into that container

Runner script:

- [scripts/run-pytest-in-docker.sh](/private/var/www/bot-manager/scripts/run-pytest-in-docker.sh)

## Remote usage

Remote system tests are triggered through:

```bash
task system-test-remote
```

Configuration comes from `.env.remote` by default.

Expected variables:

- `SYSTEM_TEST_HOST`
- `SYSTEM_TEST_USER`
- `SYSTEM_TEST_PATH`
- `SYSTEM_TEST_PORT`
- `SYSTEM_TEST_REF`
- `SYSTEM_TEST_COMMAND`

Recommended command in `.env.remote`:

```bash
SYSTEM_TEST_COMMAND=task test-system
```

Remote wrapper:

- [scripts/system-test-remote.sh](/private/var/www/bot-manager/scripts/system-test-remote.sh)

## Important constraints

- System tests still require Docker access.
- The Docker-based test runner installs `requirements-dev.txt` on each run.
- The Docker-based runner does not target the live `bridge` database by default.
- The remote server must have both Docker and `task` installed if
  `SYSTEM_TEST_COMMAND=task test-system` is used.
- These tests are deterministic only because Telegram and OpenClaw are faked.
  They do not talk to real Telegram or real AI providers.

## When to use which test

- `task test`: before merge or after significant changes
- `task test-system`: when changing booking, Telegram, escalation, tutor reply,
  OpenClaw routing, or webhook logic
- `task system-test-remote`: when validating behavior on the dedicated server
