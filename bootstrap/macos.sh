#!/usr/bin/env bash
set -euo pipefail

uname -s | grep -q "Darwin" || {
  echo "bootstrap/macos.sh is intended for macOS"
  exit 1
}

if ! xcode-select -p >/dev/null 2>&1; then
  echo "Xcode Command Line Tools are missing."
  echo "Run: xcode-select --install"
  exit 1
fi

if ! command -v brew >/dev/null 2>&1; then
  echo "Homebrew is not installed."
  echo "Install it first, then rerun."
  exit 1
fi

if [[ -x /opt/homebrew/bin/brew ]]; then
  eval "$(/opt/homebrew/bin/brew shellenv)"
elif [[ -x /usr/local/bin/brew ]]; then
  eval "$(/usr/local/bin/brew shellenv)"
fi

brew update
brew install git jq yq go-task/tap/go-task
brew install --cask docker || true

echo "Bootstrap complete."
echo "Start Docker Desktop manually, then run:"
echo "  task init"
echo "  task up"
