Wintermute (Foreman)
===================

Local, persistent work supervisor with deterministic scheduling, preemption, and an LLM executor
for next-action decisions.

Quick start
-----------
Set env vars:

```
export WINTERMUTE_DB=./wintermute.db
export WINTERMUTE_BASE_URL=http://localhost:11434/v1
export WINTERMUTE_API_KEY=ollama
export WINTERMUTE_WEB_SECRET=change-me
```

Run:

```
alembic upgrade head
python -m wintermute.web
python -m wintermute.supervisor
```

Open `http://127.0.0.1:8000` and complete the initial admin setup.

Migrations
----------

```
alembic upgrade head
```

For local/dev, `WINTERMUTE_AUTO_MIGRATE=1` (default) will auto-create tables if the
database is empty.

Tests
-----

```
python -m unittest
```
