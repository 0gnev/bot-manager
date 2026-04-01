#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  data/state/bookings \
  data/state/conversations \
  data/state/escalations \
  data/uploads \
  data/knowledge \
  data/audit
touch data/state/.gitkeep
touch data/uploads/.gitkeep
touch data/knowledge/.gitkeep
touch data/audit/.gitkeep

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example"
else
  echo ".env already exists"
fi

if [[ ! -f config/openclaw.json ]]; then
  echo "config/openclaw.json is missing"
  exit 1
fi

echo "Local init complete."
