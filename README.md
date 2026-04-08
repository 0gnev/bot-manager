# Bot Manager

Telegram-based tutor communication system with Planerka webhooks, OpenClaw AI,
and PostgreSQL runtime state.

## Quick start

cp .env.example .env
task init
task up

Main local commands:

```bash
task up
task logs
task test
task test-system
```

## Runtime layout

- `.env` is the active local env file used by `task` and `docker compose`
- operational state lives in PostgreSQL
- runtime state lives in `data/`
- Bridge writes runtime knowledge exports to `data/knowledge/`
- Bridge writes audit logs to `data/audit/`
- Bridge writes rotating runtime logs to `data/logs/bridge.log`
- Bridge stores uploaded media under `data/uploads/`
- OpenClaw runtime state lives under `data/openclaw/`
- config templates live in `config/`

Primary docs:

- GitHub Actions / deploy: [docs/github-actions.md](/private/var/www/bot-manager/docs/github-actions.md)
- local and remote test workflow: [docs/system-testing.md](/private/var/www/bot-manager/docs/system-testing.md)
- local development: [docs/local-dev.md](/private/var/www/bot-manager/docs/local-dev.md)
- PostgreSQL migration / first-rollout backup+rollback: [docs/postgresql-migration-task.md](/private/var/www/bot-manager/docs/postgresql-migration-task.md)
- operational handoff: [docs/project-handoff.md](/private/var/www/bot-manager/docs/project-handoff.md)
