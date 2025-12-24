# AGENTS.md — Foreman

## What this repo is
Foreman is a local, persistent “work supervisor” that runs an async priority queue (subsumption-style) over multiple task sources (e.g., chat/IM, Jira, GitHub) and uses an LLM only to decide the next step inside a work item. The scheduler—not the model—controls preemption, IO, credentials, and safety boundaries. Foreman speaks the OpenAI-compatible protocol so it can run against Ollama (or any compatible server) without code changes.

## Core concepts
- **TaskSource**: a pluggable watcher that periodically emits work.
- **WorkItem**: a resumable unit of work with priority + checkpointed state.
- **Supervisor**: the single asyncio event loop that owns scheduling, preemption, retries, and state persistence.
- **Executor**: the LLM-facing component that asks “what next?” and returns a structured action.
- **Tools**: constrained capabilities (GitHub/Jira/FS/etc.) exposed to the executor via explicit, typed calls.

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
- Checkpoints must be small (<256KB) and JSON-only.
- Secrets are stored via a pluggable secret backend (env vars by default; optional OS keychain later).

## Admin console (FastAPI)
The web admin console must provide:
- TaskSources CRUD: enable/disable, priority, polling interval, endpoint config
- Credentials setup (never echo secrets), test-connection actions, and scoped permissions
- Supervisor status: running/stopped/crashed, uptime, current WorkItem, last action, queue depth
- WorkItems views: queued/running/failed/done, checkpoint viewer, retry controls, “requeue”, “cancel”
- Logs: tail view (structured JSON logs preferred)

## Tooling boundaries (safety by construction)
- No “shell access” tool by default; filesystem access is via explicit allowlisted operations.
- GitHub/Jira tools must be scoped to configured orgs/projects and respect rate limits.
- The LLM cannot write arbitrary files unless the corresponding tool is enabled and path-allowlisted.
- Human override always wins: admin UI can pause supervisor, cancel items, or change priorities immediately.

## Model/provider support
Foreman uses the OpenAI-compatible Chat Completions API:
- Default: `base_url=http://localhost:11434/v1` (Ollama), `api_key=ollama`
- Models are configurable per TaskSource or globally (e.g., fast model for triage, stronger model for coding).

## Repository layout (recommended)
- `wintermute/supervisor.py` — scheduler, heap, polling, preemption, persistence
- `wintermute/sources/` — TaskSources (chat, jira, github)
- `wintermute/executor.py` — LLM adapter + structured output parsing
- `wintermute/tools/` — tool definitions + permission gating
- `wintermute/web/` — FastAPI app + UI
- `wintermute/db.py` — SQLite models/migrations
- `tests/` — unit + integration tests with mocked endpoints

## Development norms
- Keep the scheduler deterministic and testable: no hidden global state.
- Prefer typed, structured outputs from the model; reject non-conforming responses.
- Every external call (Jira/GitHub/IM) must be mockable and have timeouts + retries.
- Add metrics hooks (queue depth, task latency, error rates) early.

## Minimal local run (dev)
Set env:
- `WINTERMUTE_DB=./wintermute.db`
- `WINTERMUTE_BASE_URL=http://localhost:11434/v1`
- `WINTERMUTE_API_KEY=ollama`
Then run:
- `python -m wintermute.web` (admin console)
- `python -m wintermute.supervisor` (supervisor loop)

## Definition of done for a TaskSource
A source is “done” when it can: authenticate, poll, emit deterministic WorkItems, checkpoint, recover after restart, and be controlled via the admin console.

## License & contributions
Contributions should add tests for scheduling behavior (preemption, de-dupe, retries) and must not expand tool authority without explicit admin configuration and documentation updates.
