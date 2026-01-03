#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

PID_FILE="${WINTERMUTE_SUPERVISOR_RELAUNCHER_PID_FILE:-.runtime/supervisor.relauncher.pid}"
STOP_FLAG="${WINTERMUTE_SUPERVISOR_RELAUNCHER_STOP:-.runtime/supervisor.relauncher.stop}"

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
  ./_run_supervisor.sh
  status=$?
  set -e
  if [ "$status" -ne 0 ]; then
    if [ -f "$STOP_FLAG" ]; then
      echo "Supervisor exited with status $status; relauncher stopping."
      rm -f "$STOP_FLAG"
      break
    fi
    if [ "$status" -eq 143 ]; then
      echo "Supervisor exited with SIGTERM; restarting."
      sleep 0.5
      continue
    fi
    echo "Supervisor exited with status $status; not restarting."
    break
  fi
  sleep 0.5
done
