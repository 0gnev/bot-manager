#!/usr/bin/env bash
set -euo pipefail

REF="${1:-main}"

print_git_diagnostics() {
  echo "Deploy user/context:"
  whoami || true
  id || true
  pwd || true
  echo "Git directory permissions:"
  ls -ld . .git .git/objects .git/objects/pack 2>/dev/null || true
}

echo "Deploying ref: ${REF}"

if [ ! -f .env ]; then
  echo "Missing .env in $(pwd)"
  exit 1
fi

# Allow deployments even when the repo is accessed under a different login
# than the original owner of the working tree.
git config --global --add safe.directory "$(pwd)"

print_git_diagnostics

git fetch --all --prune

if git show-ref --verify --quiet "refs/heads/${REF}"; then
  git checkout "${REF}"
elif git show-ref --verify --quiet "refs/remotes/origin/${REF}"; then
  git checkout -B "${REF}" "origin/${REF}"
else
  git checkout "${REF}"
fi

runtime_dirty="$(git status --porcelain -- data/state data/uploads data/audit data/knowledge || true)"
if [ -n "${runtime_dirty}" ]; then
  echo "Dirty runtime files detected in the working tree:"
  printf '%s\n' "${runtime_dirty}"
  echo "These paths are server-local state and may interfere with deploys."
fi

tracked_runtime="$(git ls-files data/state data/uploads data/audit data/knowledge || true)"
if [ -n "${tracked_runtime}" ]; then
  echo "Tracked runtime files present in this checkout:"
  printf '%s\n' "${tracked_runtime}"
  echo "Tracked runtime state should be reviewed if deploys keep seeing local drift."
fi

if [ "${REF}" = "main" ]; then
  if ! git pull --ff-only origin main; then
    echo "git pull failed; dumping git permission diagnostics"
    print_git_diagnostics
    exit 128
  fi
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
