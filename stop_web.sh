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

# If no PID or PID is stale, try to find process by pattern
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
  [ -n "$PID" ] && rm -f "$PID_FILE"
  MATCH_PIDS=$(pgrep -f "daphne.*config.asgi" || true)
  if [ -z "$MATCH_PIDS" ]; then
    echo "No web PID file found at $PID_FILE and no running web process detected"
    exit 1
  fi
  PID=$(echo "$MATCH_PIDS" | head -n 1)
  echo "Web PID file stale/missing; using detected pid $PID"
  mkdir -p "$(dirname "$PID_FILE")"
  echo "$PID" > "$PID_FILE"
fi

PGID="$(ps -o pgid= -p "$PID" | tr -d ' ')"
if [ -n "$PGID" ]; then
  echo "Stopping web process group $PGID"
  kill -TERM "-$PGID" 2>/dev/null || true
else
  echo "Stopping web process $PID"
  kill -TERM "$PID" 2>/dev/null || true
fi

# Wait for process to die, then SIGKILL if needed
for i in 1 2 3 4 5; do
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "Process $PID stopped."
    exit 0
  fi
  sleep 1
done

echo "Process $PID didn't stop, sending SIGKILL"
if [ -n "$PGID" ]; then
  kill -9 "-$PGID" 2>/dev/null || true
else
  kill -9 "$PID" 2>/dev/null || true
fi
