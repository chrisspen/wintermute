#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

PID_FILE="${WINTERMUTE_WEB_RELAUNCHER_PID_FILE:-.runtime/web.relauncher.pid}"
STOP_FLAG="${WINTERMUTE_WEB_RELAUNCHER_STOP:-.runtime/web.relauncher.stop}"

mkdir -p "$(dirname "$PID_FILE")"
rm -f "$STOP_FLAG"
echo "$$" > "$PID_FILE"
trap 'rm -f "$PID_FILE"' EXIT

while true; do
  if [ -f "$STOP_FLAG" ]; then
    echo "Relauncher stop requested."
    rm -f "$STOP_FLAG"
    break
  fi
  set +e
  ./setup.sh
  ./run_web.sh
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    if [ -f "$STOP_FLAG" ]; then
      echo "Web exited with status $status; relauncher stopping."
      rm -f "$STOP_FLAG"
      break
    fi
    if [ "$status" -eq 143 ]; then
      echo "Web exited with SIGTERM; restarting."
      sleep 0.5
      continue
    fi
    echo "Web exited with status $status; not restarting."
    break
  fi
  sleep 0.5
done
