#!/usr/bin/env bash
set -euo pipefail

REF="${1:-main}"

echo "Deploying ref: ${REF}"

if [ ! -f .env ]; then
  echo "Missing .env in $(pwd)"
  exit 1
fi

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

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi

. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
python -m pytest

docker compose --env-file .env up -d --build
docker compose --env-file .env ps
