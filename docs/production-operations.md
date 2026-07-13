# Production Operations Runbook

This document is the operational runbook for the deployed Bot Manager stack.

It covers:

- normal deployment/update flow
- safe short-term shutdown to save server cost
- full backup before deleting the server or its disk
- restart and restore after downtime

Server assumptions in the current setup:

- repo path: `/opt/bot-manager`
- deploy user: `deploy`
- stack services: `postgres`, `bridge`
- health endpoint: `http://127.0.0.1:8081/health`

## 1. Normal deploy/update

GitHub Actions `CD` currently deploys by SSHing into the server and running:

```bash
cd /opt/bot-manager
bash scripts/deploy-server.sh main
```

Manual equivalent:

```bash
cd /opt/bot-manager
git pull --ff-only origin main
docker compose --env-file .env up -d --build --force-recreate
docker compose --env-file .env ps
curl -fsS http://127.0.0.1:8081/health
```

Post-deploy checks:

```bash
cd /opt/bot-manager
docker compose --env-file .env ps
docker compose --env-file .env logs --since=5m bridge
```

Expected:

- `postgres` and `bridge` are `Up`
- `bridge` answers `GET /health`
- logs show Telegram polling for both bots
- no `409 Conflict` polling errors

## 2. Decide which shutdown mode you need

There are two practical shutdown modes.

### Mode A. Stop the stack and power off the same server

Use this when:

- you only want to stop paying for compute
- the same server disk / Docker volumes will still exist
- you plan to start the same machine again later

This is the simplest mode. PostgreSQL data remains on the same disk and Docker
volume. You do not need to restore from backup later.

### Mode B. Fully archive the environment and then delete the server or disk

Use this when:

- the VM will be deleted
- attached disks/volumes may be deleted
- you may later rebuild the environment on a new machine

This mode requires a PostgreSQL dump plus a tar archive of the runtime folders.

## 3. Important downtime consequences

If the server is off:

- student Telegram messages will not be processed
- tutor Telegram messages will not be processed
- Planerka webhooks will not be handled
- incoming booking events may be lost if Planerka does not retry them long enough
- GitHub Actions `CD` will fail if it tries to SSH into the offline server

That means you should only shut the server down when downtime is acceptable.

If the public endpoint or IP will change after restart, you must also update:

- Planerka webhook URL or DNS, if it points directly to the server
- GitHub Actions secrets `DEPLOY_HOST` and `DEPLOY_KNOWN_HOSTS`

## 4. Recommended short shutdown procedure

This is the recommended sequence before turning the server off to save money.

1. Put the system into Telegram-controlled panic mode while the server is still up:

```text
/panic planned_maintenance
```

This stops new automatic student-facing replies and leaves an explicit runtime
marker in `runtime_controls`.

2. Optionally wait a minute and inspect recent logs:

```bash
cd /opt/bot-manager
docker compose --env-file .env logs --since=5m bridge
```

3. Stop the application stack:

```bash
cd /opt/bot-manager
docker compose --env-file .env down
```

4. Stop the VM / instance in your cloud provider.

If the disk and Docker volumes remain attached to that same machine, this is
enough. On next startup you will only need to bring the containers back up.

## 5. Full backup before deleting the server

If the machine or disk will be removed, take a portable backup first.

Create a dated backup directory:

```bash
cd /opt/bot-manager
ts="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="data/backups/server-shutdown/${ts}"
mkdir -p "${backup_dir}"
```

Record the exact deployed revision:

```bash
cd /opt/bot-manager
git rev-parse HEAD > "${backup_dir}/git-revision.txt"
git status --short > "${backup_dir}/git-status.txt"
```

Back up `.env`:

```bash
cd /opt/bot-manager
cp .env "${backup_dir}/.env.backup"
```

Back up PostgreSQL:

```bash
cd /opt/bot-manager
docker compose --env-file .env up -d postgres
docker compose --env-file .env exec -T postgres pg_dump -U bridge -d bridge -Fc > "${backup_dir}/bridge.dump"
```

Back up runtime folders:

```bash
cd /opt/bot-manager
tar -czf "${backup_dir}/runtime-data.tar.gz" \
  data/audit \
  data/knowledge \
  data/logs \
  data/uploads \
  data/state
```

Optional: also save a human-readable manifest:

```bash
cd /opt/bot-manager
cat > "${backup_dir}/manifest.txt" <<EOF
repo_path=/opt/bot-manager
health_url=http://127.0.0.1:8081/health
backup_created_at_utc=${ts}
EOF
```

After backup, stop the stack:

```bash
cd /opt/bot-manager
docker compose --env-file .env down
```

Then archive `${backup_dir}` outside the server before deleting the machine.

## 6. Restart after short downtime on the same server

If you kept the same disk and Docker volumes:

```bash
cd /opt/bot-manager
docker compose --env-file .env up -d --build --force-recreate
docker compose --env-file .env ps
curl -fsS http://127.0.0.1:8081/health
docker compose --env-file .env logs --since=5m bridge
```

After the stack is healthy, clear panic mode from the tutor bot:

```text
/resume maintenance_complete
```

Recommended smoke checks after restart:

1. `docker compose --env-file .env ps`
2. `curl -fsS http://127.0.0.1:8081/health`
3. send `/status` to the tutor bot
4. send one safe test question from the student bot

## 7. Restore onto a new server from backup

1. Create a new server with Docker and Docker Compose available.
2. Clone the repo into `/opt/bot-manager`.
3. Restore `.env` from backup.
4. Restore `runtime-data.tar.gz`.
5. Restore the PostgreSQL dump.
6. Start the full stack.

Concrete sequence:

```bash
git clone <repo-url> /opt/bot-manager
cd /opt/bot-manager
cp /path/to/backup/.env.backup .env
mkdir -p data
tar -xzf /path/to/backup/runtime-data.tar.gz
docker compose --env-file .env up -d postgres
until docker compose --env-file .env exec -T postgres pg_isready -U bridge -d bridge >/dev/null 2>&1; do sleep 2; done
cat /path/to/backup/bridge.dump | docker compose --env-file .env exec -T postgres pg_restore --clean --if-exists --no-owner --no-privileges -U bridge -d bridge
docker compose --env-file .env up -d --build --force-recreate
curl -fsS http://127.0.0.1:8081/health
```

After restore, verify:

- Telegram bots start polling normally
- the configured LLM provider answers (send a test student message)
- `POST /webhook/planerka` is reachable again from Planerka
- tutor `/status` shows the intended global mode

## 8. GitHub Actions while the server is intentionally offline

If the production server will be off for a while, either:

- do not merge to `main` during that downtime, or
- temporarily disable the `CD` workflow in GitHub Actions

Otherwise every deploy attempt will fail because GitHub cannot SSH into the
server.

When the server is back:

- re-enable `CD` if you disabled it
- verify `DEPLOY_HOST` and `DEPLOY_KNOWN_HOSTS`
- run one manual deploy if needed

## 9. Operational references

- [README.md](/private/var/www/bot-manager/README.md)
- [docs/project-handoff.md](/private/var/www/bot-manager/docs/project-handoff.md)
- [docs/github-actions.md](/private/var/www/bot-manager/docs/github-actions.md)
- [scripts/deploy-server.sh](/private/var/www/bot-manager/scripts/deploy-server.sh)
- [scripts/postgres-first-rollout.sh](/private/var/www/bot-manager/scripts/postgres-first-rollout.sh)
- [scripts/postgres-rollback.sh](/private/var/www/bot-manager/scripts/postgres-rollback.sh)
