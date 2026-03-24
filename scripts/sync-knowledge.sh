#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f .env.local ]]; then
  echo ".env.local not found"
  exit 1
fi

set -a
source .env.local
set +a

: "${OBSIDIAN_SOURCE_PATH:?OBSIDIAN_SOURCE_PATH is required in .env.local}"

mkdir -p data/knowledge

rsync -av --delete \
  --exclude ".obsidian" \
  --exclude ".trash" \
  "$OBSIDIAN_SOURCE_PATH"/ \
  data/knowledge/
