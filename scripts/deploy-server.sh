#!/usr/bin/env bash
set -euo pipefail

REF="${1:-main}"

echo "Deploying ref: ${REF}"

if [ ! -f .env ]; then
  echo "Missing .env in $(pwd)"
  exit 1
fi

# Allow deployments even when the repo is accessed under a different login
# than the original owner of the working tree.
git config --global --add safe.directory "$(pwd)"

git fetch --all --prune

if git show-ref --verify --quiet "refs/heads/${REF}"; then
  git checkout "${REF}"
elif git show-ref --verify --quiet "refs/remotes/origin/${REF}"; then
  git checkout -B "${REF}" "origin/${REF}"
else
  git checkout "${REF}"
fi

if [ "${REF}" = "main" ]; then
  git pull --ff-only origin main
fi

# Bind-mounted app/config files do not always trigger container recreation.
# Force a recreate so the running bot process always picks up the latest code.
docker compose --env-file .env up -d --build --force-recreate
docker compose --env-file .env ps

echo "Waiting for bridge healthcheck..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8081/health >/dev/null; then
    echo "Bridge is healthy"
    exit 0
  fi
  sleep 2
done

echo "Bridge did not become healthy in time"
docker compose --env-file .env logs --tail=200 bridge || true
exit 1
