"""Utility functions for Wintermute."""

import os


def get_environment(request):
    """Return the current environment name for display in the admin."""
    return os.environ.get("WINTERMUTE_ENV", "development")
