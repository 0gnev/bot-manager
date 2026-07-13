# Local development

## Init
cp .env.example .env
bash scripts/init-local.sh

## Start
task up

## Logs
task logs

## Restart with fresh bridge process
If code or config changes are not picked up by an existing `bridge` container:

```bash
docker compose --env-file .env up -d --build --force-recreate bridge
```

## Runtime logs
- bridge runtime log: `data/logs/bridge.log`
- recent runtime logs API: `GET /api/logs/runtime`

## Runtime data
- PostgreSQL is the operational state store
- knowledge mirror: `data/knowledge/`
- audit log: `data/audit/`
- uploads: `data/uploads/`
- legacy JSON import/backups only: `data/state/`

## Useful commands
- `task ps` — show container status
- `task down` — stop the local stack
- `task restart` — restart the stack
- `task tunnel` — start `bridge` plus the temporary Cloudflare tunnel
- `task tunnel-url` — print the current webhook tunnel URL
- tutor Telegram emergency controls:
  - `/status`
  - `/pause <reason>`
  - `/panic <reason>`
  - `/resume <reason>`

## Tests
- full Docker-backed test suite: `task test`
- Docker-backed system tests only: `task test-system`
- PostgreSQL first-rollout helper: `task pg-first-rollout`
- PostgreSQL rollback helper: `task pg-rollback`
- test workflow details: [system-testing.md](/private/var/www/bot-manager/docs/system-testing.md)
