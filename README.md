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
- Bridge writes rotating runtime logs to `data/logs/bridge.log`
- config templates live in `config/`
- GitHub Actions CI/CD setup is documented in `docs/github-actions.md`
- full migration and backup context is documented in `docs/project-handoff.md`
