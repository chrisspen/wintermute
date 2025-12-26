#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cp .env.template .env
  echo "Created .env from .env.template. Edit .env with real values, then re-run setup.sh."
  exit 0
fi

if cmp -s .env .env.template; then
  echo "Please edit .env with real values, then re-run setup.sh."
  exit 0
fi

set -a
source .env
set +a

#if ! scripts/test_api.py; then
  #echo "API test failed. Fix .env values and re-run setup.sh."
  #exit 1
#fi

alembic upgrade head

echo "Setup complete."
echo "Run web: ./run_web.sh"
echo "Run supervisor: ./run_supervisor.sh"
