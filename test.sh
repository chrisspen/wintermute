#!/bin/bash
set -e

# Run tests using pytest (preferred) with fallback to unittest
# This script can be used both locally and in CI

cd "$(dirname "$0")"

# Default venv location: ~/pyenv/wintermute
DEFAULT_VENV="$HOME/pyenv/wintermute"
VENV_DIR="${WINTERMUTE_VENV:-$DEFAULT_VENV}"

# Use venv if it exists, otherwise use system python (CI)
if [ -f "$VENV_DIR/bin/python" ]; then
    PYTHON="$VENV_DIR/bin/python"
else
    PYTHON=python
fi

# Check if pytest is available
if $PYTHON -c "import pytest" 2>/dev/null; then
    echo "Running tests with pytest..."
    $PYTHON -m pytest tests/ -x --tb=short -q
else
    echo "Running tests with unittest..."
    $PYTHON -m unittest discover -s tests -v
fi
