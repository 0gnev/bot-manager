#!/usr/bin/env bash
set -euo pipefail

mkdir -p \
  data/state/bookings \
  data/state/conversations \
  data/state/escalations \
  data/uploads \
  data/knowledge
touch data/state/.gitkeep
touch data/uploads/.gitkeep
touch data/knowledge/.gitkeep

if [[ ! -f .env.local ]]; then
  cp .env.example .env.local
  echo "Created .env.local from .env.example"
else
  echo ".env.local already exists"
fi

if [[ ! -f config/openclaw.json ]]; then
  echo "config/openclaw.json is missing"
  exit 1
fi

echo "Local init complete."
