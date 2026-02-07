"""Pytest configuration for Wintermute tests.

All tests use pytest-django's test database handling.
Production database is NEVER touched.
"""

import os

# Set environment variables before Django loads
os.environ["WINTERMUTE_DB"] = ":memory:"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import pytest
import django

# Setup Django
django.setup()


@pytest.fixture(scope="session")
def django_db_modify_db_settings(django_db_modify_db_settings_parallel_suffix):
    """Force in-memory test database."""
    from django.conf import settings
    settings.DATABASES["default"]["NAME"] = ":memory:"
    settings.DATABASES["default"]["TEST"]["NAME"] = ":memory:"
