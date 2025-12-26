#!/usr/bin/env bash
set -euo pipefail

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  cat <<'ENVEOF' > .env
WINTERMUTE_DB=./wintermute.db
WINTERMUTE_BASE_URL=http://localhost:11434/v1
WINTERMUTE_API_KEY=ollama
WINTERMUTE_WEB_SECRET=change-me
ENVEOF
fi

echo "Setup complete. Activate with: source .venv/bin/activate"
