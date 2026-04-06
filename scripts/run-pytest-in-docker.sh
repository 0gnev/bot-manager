#!/usr/bin/env bash
set -euo pipefail

PYTEST_RUNNER_IMAGE="${PYTEST_RUNNER_IMAGE:-python:3.12-slim}"
DATABASE_URL="${DATABASE_URL:-postgresql://bridge:bridge@postgres:5432/bridge}"

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

docker run --rm \
  --network "${network_name}" \
  -v "$(pwd):/workspace" \
  -w /workspace \
  -e PYTHONPATH=/workspace/app \
  -e DATABASE_URL="${DATABASE_URL}" \
  "${PYTEST_RUNNER_IMAGE}" \
  sh -lc 'pip install --no-cache-dir -r requirements-dev.txt >/tmp/pip.log && python -m pytest -q "$@"' \
  sh "$@"
