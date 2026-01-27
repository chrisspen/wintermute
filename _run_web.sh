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

PID_FILE="${WINTERMUTE_WEB_PID_FILE:-.runtime/web.pid}"
mkdir -p "$(dirname "$PID_FILE")"
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT
STARTED_FILE="${WINTERMUTE_WEB_STARTED_FILE:-.runtime/web.started}"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$STARTED_FILE"

LOG_DIR="${WINTERMUTE_LOG_DIR:-.runtime/logs}"
mkdir -p "$LOG_DIR"
export WINTERMUTE_WEB_LOG_FILE="${WINTERMUTE_WEB_LOG_FILE:-$LOG_DIR/web.log}"
LOG_CONFIG_TEMPLATE="uvicorn_log_config.ini"
LOG_CONFIG_PATH="$LOG_DIR/uvicorn_log_config.ini"
sed "s|__WINTERMUTE_WEB_LOG_FILE__|$WINTERMUTE_WEB_LOG_FILE|g" "$LOG_CONFIG_TEMPLATE" > "$LOG_CONFIG_PATH"

RELOAD_FLAG=()
if [ "${WINTERMUTE_WEB_RELOAD:-}" = "1" ]; then
  RELOAD_FLAG=(--reload)
fi

exec python -m uvicorn wintermute.web.app:create_app \
  --factory "${RELOAD_FLAG[@]}" \
  --access-log \
  --log-level info \
  --log-config "$LOG_CONFIG_PATH" \
  --host "${WINTERMUTE_WEB_HOST:-127.0.0.1}" \
  --port "${WINTERMUTE_WEB_PORT:-8000}"
