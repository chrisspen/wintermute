#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# Default venv location: ~/pyenv/wintermute
DEFAULT_VENV="$HOME/pyenv/wintermute"
VENV_DIR="${WINTERMUTE_VENV:-}"
if [ -z "$VENV_DIR" ] && [ -n "${WINTERMUTE_AGENT_NAME:-}" ]; then
  AGENT_VENV="$HOME/pyenv/wintermute-${WINTERMUTE_AGENT_NAME}"
  if [ -d "$AGENT_VENV" ]; then
    VENV_DIR="$AGENT_VENV"
  fi
fi
if [ -z "$VENV_DIR" ]; then
  VENV_DIR="$DEFAULT_VENV"
fi

if [ ! -d "$VENV_DIR" ]; then
  echo "Missing virtualenv at $VENV_DIR. Run ./setup.sh first."
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "Missing .env. Run ./setup.sh first."
  exit 1
fi

source "$VENV_DIR/bin/activate"
set -a
source .env
set +a

# Set WINTERMUTE_DB if not already set
if [ -z "${WINTERMUTE_DB:-}" ]; then
  export WINTERMUTE_DB="${SCRIPT_DIR}/wintermute.db"
fi

PID_FILE="${WINTERMUTE_WEB_PID_FILE:-.runtime/web.pid}"
mkdir -p "$(dirname "$PID_FILE")"
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
STARTED_FILE="${WINTERMUTE_WEB_STARTED_FILE:-.runtime/web.started}"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$STARTED_FILE"

LOG_DIR="${WINTERMUTE_LOG_DIR:-.runtime/logs}"
mkdir -p "$LOG_DIR"
export WINTERMUTE_WEB_LOG_FILE="${WINTERMUTE_WEB_LOG_FILE:-$LOG_DIR/web.log}"

# Use daphne for ASGI support (HTTP + WebSockets)
# hupper provides auto-reload on code changes
exec hupper -m daphne -b "${WINTERMUTE_WEB_HOST:-0.0.0.0}" -p "${WINTERMUTE_WEB_PORT:-8000}" config.asgi:application
