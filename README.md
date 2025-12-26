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

On first run, `setup.sh` creates `.venv` and copies `.env.template` to `.env`, then exits.
Fill in `.env`, then rerun `./setup.sh` to validate the OpenWebUI API, run migrations, and finish setup.

Run:

```
./run_web.sh
./run_supervisor.sh
```

Open `http://127.0.0.1:8000` and complete the initial admin setup.

Slack setup
-----------
- Open the admin UI and add the Slack bot/app tokens under "Slack Tokens".
- If you want auto-invite to new project channels, set the Slack user ID under "Slack Tokens".
- Enable the Slack source under "Slack Source".
- Restart the supervisor after updating Slack tokens so tools load the new credentials.

Projects, mappings, and sessions
--------------------------------
- Create a Project to auto-create a Slack channel (public) for that project.
- Add a VM target and Agent definition.
- Attach a VM to the project with a repo mode:
  - `mirror`: use an existing host repo path mounted into the VM at the same path.
  - `clone`: git clone from a remote URL into the VM.
- In the project Slack channel, start a session with:

```
start <projectslug> <agentslug>
```

Agent output appears in a Slack thread for that session. Replies in the thread are forwarded to the agent.

Environment
-----------
`.env` is required for local run:

- `WINTERMUTE_DB` (path to SQLite DB, default `./wintermute.db`)
- `WINTERMUTE_BASE_URL` (OpenWebUI-compatible API base, e.g. `https://openwebui.chrisspen.com/api`)
- `WINTERMUTE_API_KEY` (API key for the model server)
- `WINTERMUTE_WEB_SECRET` (session secret for the admin UI)

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
