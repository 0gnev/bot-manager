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
