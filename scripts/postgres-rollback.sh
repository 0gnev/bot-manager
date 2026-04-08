#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
BACKUP_ROOT="${BACKUP_ROOT:-data/backups/postgres-first-rollout}"
BACKUP_DIR="${BACKUP_DIR:-}"
STATE_PATH="${STATE_PATH:-data/state}"

compose=(docker compose --env-file "${ENV_FILE}")

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
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

if [[ -z "${BACKUP_DIR}" ]]; then
  if [[ ! -d "${BACKUP_ROOT}" ]]; then
    die "Backup root not found: ${BACKUP_ROOT}"
  fi
  BACKUP_DIR="$(find "${BACKUP_ROOT}" -mindepth 1 -maxdepth 1 -type d | sort | tail -1)"
fi

[[ -n "${BACKUP_DIR}" ]] || die "BACKUP_DIR is required"
manifest_path="${BACKUP_DIR}/manifest.env"
[[ -f "${manifest_path}" ]] || die "Manifest not found: ${manifest_path}"

# shellcheck disable=SC1090
source "${manifest_path}"

state_archive="${STATE_ARCHIVE_PATH:-}"
db_dump="${DB_DUMP_PATH:-}"

[[ -f "${ENV_FILE}" ]] || die "Missing env file: ${ENV_FILE}"
[[ -f "${db_dump}" ]] || die "Database dump not found: ${db_dump}"

log "Restoring backup from ${BACKUP_DIR}"

"${compose[@]}" up -d postgres
wait_for_postgres || die "postgres did not become ready"

log "Stopping bridge before restore"
"${compose[@]}" stop bridge || true

log "Restoring PostgreSQL database"
cat "${db_dump}" | "${compose[@]}" exec -T postgres \
  pg_restore --clean --if-exists --no-owner --no-privileges -U bridge -d bridge

if [[ -n "${state_archive}" && -f "${state_archive}" ]]; then
  log "Restoring legacy JSON state from ${state_archive}"
  mkdir -p "${STATE_PATH}"
  find "${STATE_PATH}" -mindepth 1 ! -name '.gitkeep' -exec rm -rf {} +
  tar -C "${STATE_PATH}" -xzf "${state_archive}"
else
  log "No legacy JSON state archive recorded in manifest"
fi

log "Starting full stack after rollback"
"${compose[@]}" up -d --build --force-recreate
"${compose[@]}" ps
wait_for_bridge || {
  "${compose[@]}" logs --tail=200 bridge || true
  die "bridge did not become healthy after rollback"
}

log "Rollback completed successfully"
