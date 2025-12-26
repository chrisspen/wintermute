Wintermute (Foreman)
===================

Local, persistent work supervisor with deterministic scheduling, preemption, and an LLM executor
for next-action decisions.

Quick start
-----------
Run setup:

```
./setup.sh
```

On first run, edit `.env` created from `.env.template`, then rerun `./setup.sh`.

Run:

```
./run_web.sh
./run_supervisor.sh
```

Open `http://127.0.0.1:8000` and complete the initial admin setup.

Slack setup
-----------
- Open the admin UI and add the Slack bot/app tokens under "Slack Tokens".
- Configure channels and enable the Slack source under "Slack Source".
- Restart the supervisor after updating Slack tokens so tools load the new credentials.

Migrations
----------

```
alembic upgrade head
```

Tests
-----

```
python -m unittest
```
