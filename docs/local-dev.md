# Local development

## Init
cp .env.example .env.local
bash scripts/init-local.sh

## Start
docker compose --env-file .env.local up -d --build

## Logs
docker compose --env-file .env.local logs -f

## Runtime data
- knowledge mirror: `data/knowledge/`
- audit log: `data/audit/`
