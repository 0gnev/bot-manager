#!/usr/bin/env bash
set -euo pipefail

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "Missing required command: $1"
    exit 1
  }
}

need git
need jq
need yq
need task
need docker

docker info >/dev/null 2>&1 || {
  echo "Docker is installed, but daemon is not running."
  echo "Start Docker Desktop and rerun."
  exit 1
}

echo "Environment looks good."
