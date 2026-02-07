from __future__ import annotations

import os
import sys
from logging.config import fileConfig

sys.path.insert(0, os.path.abspath(os.getcwd()))

from alembic import context # pylint: disable=no-name-in-module
from sqlalchemy import engine_from_config, pool

# Note: Wintermute now uses Django ORM with Django migrations.
# Alembic/SQLAlchemy is no longer used. This file is kept for historical reference.
try:
    from sqlalchemy.orm import declarative_base
    Base = declarative_base()
except ImportError:
    Base = None

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url() -> str:
    return os.path.expanduser(os.environ.get("WINTERMUTE_DB", "~/dbs/wintermute/wintermute.db"))


def run_migrations_offline() -> None:
    url = f"sqlite:///{get_url()}"
    context.configure(
        url=url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = f"sqlite:///{get_url()}"
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
