#!/usr/bin/env bash
set -euo pipefail

PYTEST_RUNNER_IMAGE="${PYTEST_RUNNER_IMAGE:-python:3.12-slim}"
PYTEST_DB_NAME="${PYTEST_DB_NAME:-bridge_test}"
DATABASE_URL="${DATABASE_URL:-postgresql://bridge:bridge@postgres:5432/${PYTEST_DB_NAME}}"

case "${PYTEST_DB_NAME}" in
  ""|*[!A-Za-z0-9_]*)
    echo "Invalid PYTEST_DB_NAME: ${PYTEST_DB_NAME}"
    exit 1
    ;;
esac

docker compose up -d postgres

postgres_container_id="$(docker compose ps -q postgres)"
if [ -z "${postgres_container_id}" ]; then
  echo "Could not find postgres container"
  exit 1
fi

network_name="$(
  docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' \
    "${postgres_container_id}" | head -n1 | tr -d '[:space:]'
)"

if [ -z "${network_name}" ]; then
  echo "Could not determine docker network for postgres"
  exit 1
fi

echo "Waiting for postgres readiness..."
ready=0
for _ in $(seq 1 60); do
  if docker exec "${postgres_container_id}" pg_isready -U bridge -d bridge >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 1
done

if [ "${ready}" -ne 1 ]; then
  echo "Postgres did not become ready in time"
  docker compose logs --tail=200 postgres || true
  exit 1
fi

if ! docker exec "${postgres_container_id}" psql -U bridge -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='${PYTEST_DB_NAME}'" | grep -q 1; then
  docker exec "${postgres_container_id}" psql -U bridge -d postgres -c \
    "CREATE DATABASE ${PYTEST_DB_NAME}"
fi

docker run --rm \
  --network "${network_name}" \
  -v "$(pwd):/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace/app \
  -e DATABASE_URL="${DATABASE_URL}" \
  "${PYTEST_RUNNER_IMAGE}" \
  sh -lc 'pip install --no-cache-dir -r requirements-dev.txt >/tmp/pip.log && python -m pytest -q "$@"' \
  sh "$@"
