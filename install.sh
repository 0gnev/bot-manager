#!/usr/bin/env bash
#
# Bot Manager installer.
#
#   git clone <repo> && cd bot-manager && ./install.sh
#
# Installs the `botmgr` command (via pipx when available, otherwise a local
# virtualenv with a symlink) and checks for Docker. Everything else is done
# interactively by `botmgr setup`.

set -euo pipefail

cyan()  { printf '\033[1;36m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m✓ %s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m✗ %s\033[0m\n' "$*"; }
warn()  { printf '\033[1;33m! %s\033[0m\n' "$*"; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

cyan "──────────────────────────────────────────"
cyan "  Bot Manager installer"
cyan "──────────────────────────────────────────"

# ── Python ────────────────────────────────────────────────────────────────────
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
      PYTHON="$candidate"
      break
    fi
  fi
done
if [[ -z "$PYTHON" ]]; then
  red "Python 3.11+ is required. Install it from python.org or your package manager."
  exit 1
fi
green "Python found: $($PYTHON --version)"

# ── Docker ────────────────────────────────────────────────────────────────────
if command -v docker >/dev/null 2>&1; then
  green "Docker found"
else
  warn "Docker is not installed. The bot runs in Docker — install Docker Desktop"
  warn "from docker.com/products/docker-desktop, then re-run botmgr doctor."
fi

# ── Install the botmgr command ────────────────────────────────────────────────
if command -v pipx >/dev/null 2>&1; then
  pipx install --force "$ROOT" >/dev/null
  green "Installed botmgr with pipx"
else
  VENV="$ROOT/.botmgr-venv"
  "$PYTHON" -m venv "$VENV"
  "$VENV/bin/pip" install --quiet --upgrade pip
  "$VENV/bin/pip" install --quiet "$ROOT"
  BIN_DIR="$HOME/.local/bin"
  mkdir -p "$BIN_DIR"
  ln -sf "$VENV/bin/botmgr" "$BIN_DIR/botmgr"
  green "Installed botmgr into $VENV (symlinked to $BIN_DIR/botmgr)"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH — add:  export PATH=\"\$PATH:$BIN_DIR\"" ;;
  esac
fi

# Remember the project folder so botmgr works from anywhere.
BOTMGR_ENV_LINE="export BOTMGR_HOME=\"$ROOT\""
for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
  if [[ -f "$rc" ]] && ! grep -qF 'BOTMGR_HOME' "$rc"; then
    printf '\n# Bot Manager project folder\n%s\n' "$BOTMGR_ENV_LINE" >> "$rc"
  fi
done
export BOTMGR_HOME="$ROOT"
green "BOTMGR_HOME set to $ROOT"

echo
cyan "Done! Next steps:"
echo "  1. Open a new terminal (or run: $BOTMGR_ENV_LINE)"
echo "  2. botmgr setup    — interactive configuration"
echo "  3. botmgr doctor   — verify everything"
echo "  4. botmgr up       — start the bot"
