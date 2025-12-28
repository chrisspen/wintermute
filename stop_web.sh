#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

PID_FILE="${WINTERMUTE_WEB_PID_FILE:-.runtime/web.pid}"
STOP_FLAG="${WINTERMUTE_WEB_RELAUNCHER_STOP:-.runtime/web.relauncher.stop}"
RELAUNCHER_PID_FILE="${WINTERMUTE_WEB_RELAUNCHER_PID_FILE:-.runtime/web.relauncher.pid}"

if [ -f "$RELAUNCHER_PID_FILE" ]; then
  echo "Stop relauncher requested."
  mkdir -p "$(dirname "$STOP_FLAG")"
  touch "$STOP_FLAG"
fi

PID=""
if [ -f "$PID_FILE" ]; then
  PID="$(cat "$PID_FILE")"
fi

if [ -z "$PID" ]; then
  MATCH_PIDS=$(pgrep -f "uvicorn wintermute.web.app:create_app" || true)
  if [ -z "$MATCH_PIDS" ]; then
    echo "No web PID file found at $PID_FILE and no running web process detected"
    exit 1
  fi
  PID=$(echo "$MATCH_PIDS" | head -n 1)
  echo "Web PID file missing; using detected pid $PID"
  mkdir -p "$(dirname "$PID_FILE")"
  echo "$PID" > "$PID_FILE"
fi

if ! kill -0 "$PID" 2>/dev/null; then
  echo "Web process $PID is not running"
  rm -f "$PID_FILE"
  exit 1
fi

PGID="$(ps -o pgid= -p "$PID" | tr -d ' ')"
if [ -n "$PGID" ]; then
  echo "Stopping web process group $PGID"
  kill -TERM "-$PGID" 2>/dev/null || true
else
  echo "Stopping web process $PID"
  kill -TERM "$PID" 2>/dev/null || true
fi
