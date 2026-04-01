# Bot Manager

Local/dev skeleton for Bot Manager.

## Quick start

cp .env.example .env.local
task init
task up

## Notes

- `.env.local` is not committed
- runtime state lives in `data/`
- Bridge writes runtime knowledge exports to `data/knowledge/`
- Bridge writes audit logs to `data/audit/`
- config templates live in `config/`
