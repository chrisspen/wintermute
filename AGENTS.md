# AGENTS.md — Wintermute

## What this repo is
Wintermute is a local, persistent “work supervisor” that runs an async priority queue (subsumption-style) over multiple task sources (e.g., chat/IM, Jira, GitHub) and uses an LLM only to decide the next step inside a work item. The scheduler—not the model—controls preemption, IO, credentials, and safety boundaries. Wintermute speaks the OpenAI-compatible protocol so it can run against Ollama (or any compatible server) without code changes.

## Core concepts
- **TaskSource**: a pluggable watcher that periodically emits work.
- **WorkItem**: a resumable unit of work with priority + checkpointed state.
- **Supervisor**: the single asyncio event loop that owns scheduling, preemption, retries, and state persistence.
- **Executor**: the LLM-facing component that asks “what next?” and returns a structured action.
- **Tools**: constrained capabilities (GitHub/Jira/FS/etc.) exposed to the executor via explicit, typed calls.
- **Project**: top-level workspace with Slack channel and linked VM targets.
- **Ticket**: lightweight work item tracking (title, status, estimate, assigned).
- **Agent**: CLI agent definition (command + required SSH options).
- **AgentSession**: persistent agent session (tmux or MCP) for a project VM.
- **Project VM Mapping**: link between a Project and a VM target with repo mode settings.
- **Repo Resource**: reusable repo checkout path tied to a project/VM/session (clone mode pool).
- **GitHub Source**: per-repo config (with token) that polls issues and emits work items.
- **GitHub Token**: per-user credential used by one or more GitHub sources.
- **GitLab Source**: per-project config (with token) that polls issues and emits work items.
- **GitLab Token**: per-user credential used by one or more GitLab sources.
- **Standup Source**: scheduled daily standup prompts for running agent sessions.
- **API Token**: scoped credential for REST access with per-model CRUD permissions.

## Interfaces (contract-first)
### TaskSource
A TaskSource must be deterministic and side-effect free in `poll()`, and must not call the LLM.
- `id: str`
- `enabled: bool`
- `base_priority: int` (lower number = higher priority)
- `poll(ctx) -> list[WorkItemDraft]`
  Emits drafts that the supervisor de-dupes into WorkItems.

### WorkItem
A WorkItem is resumable and safe to preempt at any await point.
- `work_id: str` (stable, deterministic key for de-dupe)
- `priority: int` (effective priority; may be derived from base_priority + item metadata)
- `source_id: str`
- `status: queued|running|blocked|done|failed`
- `checkpoint: dict` (JSON-serializable state; *only* source-owned state lives here)
- `resume(ctx) -> Awaitable[None]` (called by supervisor; must checkpoint frequently)

### LLM decision API (pure function)
The LLM is invoked only through `decide_next_action(state, observation, tool_schema)`.
It must return one of:
- `{"type":"yield","reason":...}` (pause until next poll / external event)
- `{"type":"tool","name":..., "args":{...}}` (single tool call request)
- `{"type":"update","patch":{...}}` (checkpoint update)
- `{"type":"done","summary":...}`
- `{"type":"escalate","priority":int,"reason":...}` (raise urgency; scheduler decides)

## Scheduling & preemption rules
1. Supervisor maintains a min-heap ordered by `(priority, created_at)`.
2. Any newly arrived WorkItem with strictly higher priority preempts the current one.
3. Preemption is cooperative: the running WorkItem must checkpoint and return to the supervisor quickly.
4. Retries use exponential backoff per WorkItem; permanent failures are recorded with last error + traceback.
5. The supervisor is the only component allowed to start/stop WorkItems.

## State & storage
- **SQLite** is the source of truth for: TaskSources config, WorkItems, checkpoints, run history, and credentials references.
- **SQLAlchemy ORM** is used for persistence with **Alembic** migrations (see `alembic/` and `alembic.ini`).
- Checkpoints must be small (<256KB) and JSON-only.
- Secrets are stored via a pluggable secret backend (env vars by default; optional OS keychain later).

## Admin console (FastAPI)
The web admin console provides:
- Session-based admin login (salted password hash stored in SQLite).
- TaskSources CRUD: enable/disable, priority, polling interval, endpoint config.
- Credentials setup (never echo secrets), test-connection actions, and scoped permissions.
- Supervisor status: running/stopped/crashed, uptime, current WorkItem, last action, queue depth.
- WorkItems views: queued/running/failed/done, checkpoint viewer, retry controls, “requeue”, “cancel”.
- Logs: tail view (structured JSON logs preferred).
- Projects, Tickets, VM targets, Agents, and Project VM mappings.
- Ticket auto-start source for internal tickets.
- Slack channel per project; Slack command `start <projectslug> <agentslug>` to launch sessions.
- Project VM mapping edit page with repo mode/path/url controls.
- Repo resource pool (clone mode) with per-project `max_repo_resources` and daily cleanup for unused clones (default 30 days).
- Repo resource cleanup age is configured via `WINTERMUTE_REPO_RESOURCE_TTL_DAYS` (default 30).
- GitHub token storage and GitHub source configuration (multiple repos, optional auto-start).
- GitHub session output: `PUBLIC:` lines become comments pending approval; `NOTE:` lines stay internal.
- GitHub session output: `BLOCKER:` lines mark sessions blocked and move tickets to needs-feedback.
- Approved public comments are auto-dispatched to GitHub by the comment dispatch source.
- GitLab token storage and GitLab source configuration (multiple projects, optional auto-start).
- GitLab session output: `PUBLIC:` lines become comments pending approval; `NOTE:` lines stay internal.
- GitLab session output: `BLOCKER:` lines mark sessions blocked and move tickets to needs-feedback.
- Approved public comments are auto-dispatched to GitLab by the comment dispatch source.
- Standup scheduling (daily standup source) with time, timezone, and optional Slack channel; agents reply with `STANDUP:` lines.
- API tokens with CRUD permissions and optional env export (`WINTERMUTE_ADMIN_API_TOKEN`).
- Admin API endpoints can restart web/supervisor (requires Admin update permission).
- Relaunchers (`run_web.sh`, `run_supervisor.sh`) keep processes alive and restart on SIGTERM (exit 143).
- Direct runners (`_run_web.sh`, `_run_supervisor.sh`) run once without relaunch.
- Stop scripts (`stop_web.sh`, `stop_supervisor.sh`) request a clean shutdown.
- PID/status endpoint: `GET /api/admin/pids`.
- Log tail endpoint: `GET /api/admin/logs?service=web|supervisor&lines=200`.
- `.codex/token` stores `WINTERMUTE_ADMIN_API_TOKEN=<token>`; strip the prefix when sending the bearer token.

## Tooling boundaries (safety by construction)
- No “shell access” tool by default; filesystem access is via explicit allowlisted operations.
- GitHub/Jira tools must be scoped to configured orgs/projects and respect rate limits.
- The LLM cannot write arbitrary files unless the corresponding tool is enabled and path-allowlisted.
- Human override always wins: admin UI can pause supervisor, cancel items, or change priorities immediately.

## Model/provider support
Wintermute uses the OpenAI-compatible Chat Completions API:
- Default: `base_url=http://localhost:11434/v1` (Ollama), `api_key=ollama`
- Models are configurable per TaskSource or globally (e.g., fast model for triage, stronger model for coding).
- OpenWebUI-compatible API is supported via `WINTERMUTE_BASE_URL` + `WINTERMUTE_API_KEY`.
- GitHub tools are available when GitHub tokens are stored.

## Repository layout (recommended)
- `wintermute/supervisor.py` — scheduler, heap, polling, preemption, persistence
- `wintermute/sources/` — TaskSources (chat, jira, github, slack, sessions)
- `wintermute/sources/gitlab.py` — GitLab issues TaskSource
- `wintermute/sources/standup.py` — Daily standup TaskSource
- `wintermute/executor.py` — LLM adapter + structured output parsing
- `wintermute/tools/` — tool definitions + permission gating
- `wintermute/tools/gitlab.py` — GitLab API tools
- `wintermute/web/` — FastAPI app + UI
- `wintermute/db.py` — SQLite ORM models/migrations
- `wintermute/runner.py` — SSH + tmux session runner for agent sessions
- `wintermute/mcp_client.py` — MCP stdio client for Codex sessions
- `tests/` — unit + integration tests with mocked endpoints
- `alembic/` + `alembic.ini` — SQLAlchemy migrations
- `setup.sh`, `run_web.sh`, `run_supervisor.sh`, `_run_web.sh`, `_run_supervisor.sh` — local setup and runners

## Development norms
- Keep the scheduler deterministic and testable: no hidden global state.
- Prefer typed, structured outputs from the model; reject non-conforming responses.
- Every external call (Jira/GitHub/IM) must be mockable and have timeouts + retries.
- Add metrics hooks (queue depth, task latency, error rates) early.
- UI rule: no HTML or CSS embedded in Python files; use template files and static assets.
- UI rule: never add margin-bottom directly to input/select/textarea; use container spacing.
- Never run commands inside the user's `.venv` or upgrade it directly; use the restart/setup flow instead.
- Run tests with `.venv/bin/python -m pytest tests/ -x --tb=short` (do not activate the venv).

## VM networking
When running as an agent inside a VM, the web server runs on the host machine. Use the gateway IP (typically `192.168.123.1`) to reach host services:
- Find gateway: `ip route | grep default | awk '{print $3}'`
- API calls: `curl -H "Authorization: Bearer $TOKEN" http://192.168.123.1:8000/api/...`
- API token: read from `.codex/token` (strip the `WINTERMUTE_ADMIN_API_TOKEN=` prefix)
- After completing web server changes you're confident will work, restart via: `curl -s -X POST -H "Authorization: Bearer $TOKEN" http://192.168.123.1:8000/api/admin/restart-web`

## Minimal local run (dev)
Run setup:
- `./setup.sh` (first run creates `.venv` and `.env`, second run tests API and runs migrations)
Then run:
- `./run_web.sh` (admin console)
- `./run_supervisor.sh` (supervisor loop)

## Agent testing
When running local tests as an agent, prefer a per-agent venv at `.<agent>/.venv` (use `./setup.sh --agent-name <agent>` or `./setup.sh --venv-dir .<agent>/.venv`). At runtime, set `WINTERMUTE_AGENT_NAME=<agent>` or `WINTERMUTE_VENV=.<agent>/.venv`.
Use the agent-specific venv for this environment: `.codex/.venv` (run `./setup.sh --venv-dir .codex/.venv`).

When testing `./_run_web.sh` or `./_run_supervisor.sh`, they block; run with `PYTHONUNBUFFERED=1` and stop once the “ready” log line appears to avoid waiting on timeouts.

## tmux note
To make detach consistent with `Ctrl-a` + `d`, add this to `~/.tmux.conf`:
```
unbind-key C-b
set-option -g prefix C-a
bind-key C-a send-prefix
```

## Definition of done for a TaskSource
A source is “done” when it can: authenticate, poll, emit deterministic WorkItems, checkpoint, recover after restart, and be controlled via the admin console.

## License & contributions
Contributions should add tests for scheduling behavior (preemption, de-dupe, retries) and must not expand tool authority without explicit admin configuration and documentation updates.

## Session Reload (IMPORTANT)
At session start, read these files to restore context. Before shutdown, update each file so the next session can resume cleanly:
```bash
cat .<agent_name>/STATE.md
cat .<agent_name>/DECISIONS.md
cat .<agent_name>/TODO.md
cat .<agent_name>/CONTEXT.md
```

.<agent_name>/
├── STATE.md        # current goals, constraints, known issues
├── DECISIONS.md    # why things were done a certain way
├── TODO.md         # pending tasks
└── CONTEXT.md      # compressed narrative summary

## Logging

Log all your actions in .<agent_name>/log.txt.

A log entry will be in the format of "<datetime>,<description>"

e.g. If you are Codex write a log entry for each action in .codex/log.txt.

e.g. If you are Gemini write a log entry for each action in .gemini/log.txt.

e.g. If you are Claude write a log entry for each action in .claude/log.txt.
