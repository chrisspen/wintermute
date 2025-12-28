#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

VENV_DIR="${WINTERMUTE_VENV:-}"
if [ -z "$VENV_DIR" ] && [ -n "${WINTERMUTE_AGENT_NAME:-}" ]; then
  if [ -d ".${WINTERMUTE_AGENT_NAME}/.venv" ]; then
    VENV_DIR=".${WINTERMUTE_AGENT_NAME}/.venv"
  fi
fi
if [ -z "$VENV_DIR" ]; then
  VENV_DIR=".venv"
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

PID_FILE="${WINTERMUTE_SUPERVISOR_PID_FILE:-.runtime/supervisor.pid}"
mkdir -p "$(dirname "$PID_FILE")"
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
STARTED_FILE="${WINTERMUTE_SUPERVISOR_STARTED_FILE:-.runtime/supervisor.started}"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$STARTED_FILE"

exec python -m wintermute.supervisor
