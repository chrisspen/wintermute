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

LOG_DIR="${WINTERMUTE_LOG_DIR:-.runtime/logs}"
mkdir -p "$LOG_DIR"
export WINTERMUTE_SUPERVISOR_LOG_FILE="${WINTERMUTE_SUPERVISOR_LOG_FILE:-$LOG_DIR/supervisor.log}"

PID_FILE="${WINTERMUTE_SUPERVISOR_PID_FILE:-.runtime/supervisor.pid}"
mkdir -p "$(dirname "$PID_FILE")"
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
STARTED_FILE="${WINTERMUTE_SUPERVISOR_STARTED_FILE:-.runtime/supervisor.started}"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$STARTED_FILE"

exec python -m wintermute.supervisor
