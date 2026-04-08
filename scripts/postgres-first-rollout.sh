#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
STATE_PATH="${STATE_PATH:-data/state}"
BACKUP_ROOT="${BACKUP_ROOT:-data/backups/postgres-first-rollout}"
DATABASE_URL="${DATABASE_URL:-postgresql://bridge:bridge@postgres:5432/bridge}"
IMPORT_LEGACY_STATE="${IMPORT_LEGACY_STATE:-auto}" # auto|always|never

compose=(docker compose --env-file "${ENV_FILE}")

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_file() {
  local path="$1"
  [[ -f "${path}" ]] || die "Missing required file: ${path}"
}

has_legacy_state() {
  [[ -d "${STATE_PATH}" ]] || return 1
  find "${STATE_PATH}" -mindepth 1 ! -name '.gitkeep' -print -quit | grep -q .
}

wait_for_postgres() {
  log "Waiting for postgres readiness..."
  for _ in $(seq 1 30); do
    if "${compose[@]}" exec -T postgres pg_isready -U bridge -d bridge >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_bridge() {
  log "Waiting for bridge healthcheck..."
  for _ in $(seq 1 30); do
    if curl -fsS http://127.0.0.1:8081/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

db_has_runtime_state() {
  local count
  count="$("${compose[@]}" exec -T postgres psql -U bridge -d bridge -Atc \
    "SELECT
       COALESCE((SELECT count(*) FROM bookings), 0) +
       COALESCE((SELECT count(*) FROM messages), 0) +
       COALESCE((SELECT count(*) FROM escalations), 0) +
       COALESCE((SELECT count(*) FROM approvals), 0);" \
  )"
  [[ "${count:-0}" != "0" ]]
}

require_file "${ENV_FILE}"

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_dir="${BACKUP_ROOT}/${timestamp}"
manifest_path="${backup_dir}/manifest.env"
state_archive_path="${backup_dir}/legacy-state.tar.gz"
db_dump_path="${backup_dir}/bridge-pre-rollout.dump"

mkdir -p "${backup_dir}"

log "Starting PostgreSQL first-rollout backup/import flow"
log "Backup directory: ${backup_dir}"

state_backup_created="no"
if has_legacy_state; then
  log "Backing up legacy JSON state from ${STATE_PATH}"
  tar -C "${STATE_PATH}" -czf "${state_archive_path}" .
  state_backup_created="yes"
else
  log "No legacy JSON state detected under ${STATE_PATH}"
fi

log "Starting postgres service"
"${compose[@]}" up -d postgres
wait_for_postgres || die "postgres did not become ready"

log "Backing up current PostgreSQL database"
"${compose[@]}" exec -T postgres pg_dump -U bridge -d bridge -Fc > "${db_dump_path}"

import_ran="no"
case "${IMPORT_LEGACY_STATE}" in
  never)
    log "Skipping legacy state import (IMPORT_LEGACY_STATE=never)"
    ;;
  always)
    if has_legacy_state; then
      log "Running forced legacy JSON import into PostgreSQL"
      "${compose[@]}" run --rm --no-deps bridge \
        python -m scripts.import_json_state \
        --state-path /workspace/data/state \
        --database-url "${DATABASE_URL}"
      import_ran="yes"
    else
      log "Skipping forced import because no legacy JSON state exists"
    fi
    ;;
  auto)
    if has_legacy_state && ! db_has_runtime_state; then
      log "Database is empty; importing legacy JSON state"
      "${compose[@]}" run --rm --no-deps bridge \
        python -m scripts.import_json_state \
        --state-path /workspace/data/state \
        --database-url "${DATABASE_URL}"
      import_ran="yes"
    else
      log "Skipping import in auto mode (either no legacy state or DB already contains runtime rows)"
    fi
    ;;
  *)
    die "Unsupported IMPORT_LEGACY_STATE=${IMPORT_LEGACY_STATE}. Use auto, always, or never."
    ;;
esac

log "Starting full stack"
"${compose[@]}" up -d --build --force-recreate
"${compose[@]}" ps
wait_for_bridge || {
  "${compose[@]}" logs --tail=200 bridge || true
  die "bridge did not become healthy after first-rollout flow"
}

cat > "${manifest_path}" <<EOF
TIMESTAMP=${timestamp}
ENV_FILE=${ENV_FILE}
STATE_PATH=${STATE_PATH}
DATABASE_URL=${DATABASE_URL}
IMPORT_LEGACY_STATE=${IMPORT_LEGACY_STATE}
STATE_BACKUP_CREATED=${state_backup_created}
STATE_ARCHIVE_PATH=${state_archive_path}
DB_DUMP_PATH=${db_dump_path}
IMPORT_RAN=${import_ran}
EOF

log "First-rollout flow completed"
log "Manifest: ${manifest_path}"
log "Rollback command:"
log "  BACKUP_DIR=${backup_dir} bash scripts/postgres-rollback.sh"
