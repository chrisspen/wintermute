#!/bin/bash
set -e

# Run tests using pytest (preferred) with fallback to unittest
# This script can be used both locally and in CI

cd "$(dirname "$0")"

# Use .venv if it exists (local), otherwise use system python (CI)
if [ -f .venv/bin/python ]; then
    PYTHON=.venv/bin/python
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
