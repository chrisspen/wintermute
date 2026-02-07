#!/usr/bin/env bash
# Installs and configures the Python virtual environment.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$SCRIPT_DIR"

# Default venv location: ~/pyenv/wintermute
DEFAULT_VENV="$HOME/pyenv/wintermute"
VENV_DIR="${WINTERMUTE_VENV:-$DEFAULT_VENV}"
if [ "${1:-}" = "--agent-name" ] && [ -n "${2:-}" ]; then
  VENV_DIR="$HOME/pyenv/wintermute-${2}"
  shift 2
elif [ "${1:-}" = "--venv-dir" ] && [ -n "${2:-}" ]; then
  VENV_DIR="$2"
  shift 2
fi

echo "Using Python virtualenv directory: $VENV_DIR"

if [ ! -d "$VENV_DIR" ]; then
  mkdir -p "$(dirname "$VENV_DIR")"
  python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
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

# Ensure database directory exists
DEFAULT_DB="$HOME/dbs/wintermute/wintermute.db"
DB_PATH="${WINTERMUTE_DB:-$DEFAULT_DB}"
DB_PATH="${DB_PATH/#\~/$HOME}"
mkdir -p "$(dirname "$DB_PATH")"

# Run all Django migrations
python manage.py migrate --fake-initial --noinput

echo "Setup complete. Venv: $VENV_DIR"
echo "Run web: ./run_web.sh"
echo "Run supervisor: ./run_supervisor.sh"
