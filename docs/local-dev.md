# Local development

## Init
cp .env.example .env.local
bash scripts/init-local.sh

## Start
docker compose --env-file .env.local up -d --build

## Logs
docker compose --env-file .env.local logs -f

## Runtime logs
- bridge runtime log: `data/logs/bridge.log`
- recent runtime logs API: `GET /api/logs/runtime`

## Runtime data
- knowledge mirror: `data/knowledge/`
- audit log: `data/audit/`

## Tests
- full Docker-backed test suite: `task test`
- Docker-backed system tests only: `task test-system`
- test workflow details: [system-testing.md](/private/var/www/bot-manager/docs/system-testing.md)
