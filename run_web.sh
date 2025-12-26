#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  echo "Missing .venv. Run ./setup.sh first."
  exit 1
fi

if [ ! -f ".env" ]; then
  echo "Missing .env. Run ./setup.sh first."
  exit 1
fi

source .venv/bin/activate
set -a
source .env
set +a

python -m uvicorn wintermute.web.app:create_app --factory --reload --host "${WINTERMUTE_WEB_HOST:-127.0.0.1}" --port "${WINTERMUTE_WEB_PORT:-8000}"
