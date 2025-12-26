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
- Enable the Slack source under "Slack Source".
- Restart the supervisor after updating Slack tokens so tools load the new credentials.

Projects & sessions
-------------------
- Create a Project to auto-create a Slack channel (public) for that project.
- Add a VM target and Agent definition.
- Attach a VM to the project with a repo mode (mirror or clone).
- In the project Slack channel, start a session with:

```
start <projectslug> <agentslug>
```

Agent output appears in a Slack thread for that session. Replies in the thread are forwarded to the agent.

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
