"""Supervisor loop for scheduling and preempting work items."""

from __future__ import annotations

import asyncio
import heapq
import os
import re
import traceback
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Iterable, Optional

from wintermute import __version__
from wintermute.db import Database, WorkItemRecord, utc_now
from wintermute.executor import Executor
from wintermute.sources.base import TaskSource, WorkItemContext, WorkItemBlocked
from wintermute.sources.demo import DemoSource
from wintermute.sources.github import GitHubIssuesSource
from wintermute.sources.gitlab import GitLabIssuesSource
from wintermute.sources.comment_dispatch import CommentDispatchSource
from wintermute.sources.slack import (
    SlackSource,
    SLACK_APP_TOKEN_NAME,
    SLACK_BOT_TOKEN_NAME,
    SLACK_PROVIDER,
)
from wintermute.sources.sessions import SessionSource
from wintermute.sources.tickets import TicketAutoStartSource
from wintermute.sources.standup import StandupSource
from wintermute.sources.registry import all_sources, register
from wintermute.tools.base import ToolRegistry
from wintermute.runner import build_ssh_spec, delete_repo_path
from wintermute.tools.fs import ReadFileTool
from wintermute.tools.github import (
    GitHubCommentIssueTool,
    GitHubGetIssueTool,
    GitHubListIssuesTool,
)
from wintermute.tools.gitlab import (
    GitLabCommentIssueTool,
    GitLabGetIssueTool,
    GitLabListIssuesTool,
)
from wintermute.tools.slack import SlackPostMessageTool


def _parse_allowlist(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


logger = logging.getLogger(__name__)


@dataclass
class SupervisorStatus:
    running: bool
    started_at: str
    current_work_id: Optional[str]
    queue_depth: int


class Supervisor:

    def __init__(
        self,
        db: Database,
        sources: Iterable[TaskSource],
        executor: Executor,
        tools: ToolRegistry,
        max_attempts: int = 5,
    ) -> None:
        self.db = db
        self.sources = list(sources)
        self.executor = executor
        self.tools = tools
        self.max_attempts = max_attempts
        self._queue: list[tuple[int, str, str]] = []
        self._current_work_id: Optional[str] = None
        self._preempt_event = asyncio.Event()
        self._stop_event = asyncio.Event()
        self._started_at = utc_now()
        self._last_poll: dict[str, float] = {}
        self._tools_version: tuple[str, str] = ("", "")
        self._slack_signature: tuple[Optional[str], Optional[str]] = (None, None)
        self._last_repo_cleanup: float = 0.0

    def status(self) -> SupervisorStatus:
        return SupervisorStatus(
            running=not self._stop_event.is_set(),
            started_at=self._started_at,
            current_work_id=self._current_work_id,
            queue_depth=len(self._queue),
        )

    async def run(self) -> None:
        self.db.initialize()
        self._ensure_task_sources()
        try:
            while not self._stop_event.is_set():
                logger.debug("Supervisor loop tick.")
                await self._refresh_runtime()
                await self._poll_sources()
                self._update_state("polled sources")
                await self._refresh_queue()
                self._update_state("refreshed queue")
                await self._cleanup_repo_resources()
                await self._cycle_sprints()
                if not self._current_work_id:
                    await self._run_next()
                self._update_state("idle")
                await asyncio.sleep(1)
        finally:
            self._stop_event.set()
            self._update_state("stopped")

    def stop(self) -> None:
        self._stop_event.set()

    def _ensure_task_sources(self) -> None:
        existing = {row.id for row in self.db.list_task_sources()}
        for source in self.sources:
            if source.id in existing:
                continue
            self.db.upsert_task_source(
                source.id,
                source.enabled,
                source.base_priority,
                source.poll_interval_seconds,
                config={},
            )

    async def _poll_sources(self) -> None:
        rows = {row.id: row for row in self.db.list_task_sources()}
        now = datetime.now(timezone.utc).timestamp()
        for source in self.sources:
            row = rows.get(source.id)
            if not row:
                continue
            logger.debug("Polling source %s", source.id)
            last_poll = self._last_poll.get(source.id, 0.0)
            if now - last_poll < row.poll_interval_seconds:
                continue
            self._last_poll[source.id] = now
            drafts = await source.poll({"db": self.db})
            logger.debug("Source %s emitted %d drafts", source.id, len(drafts))
            for draft in drafts:
                inserted = self.db.insert_work_item_if_absent(
                    draft.work_id,
                    draft.source_id,
                    draft.priority,
                    draft.checkpoint,
                )
                if inserted:
                    logger.info("Queued work item %s", draft.work_id)
                    if self._current_work_id:
                        current = self.db.get_work_item(self._current_work_id)
                        if current and draft.priority < current.priority:
                            self._preempt_event.set()

    async def _refresh_queue(self) -> None:
        self._queue.clear()
        for item in self.db.fetch_ready_work_items(utc_now()):
            if item.work_id == self._current_work_id:
                continue
            # Use run_after as tiebreaker (not created_at) for fair scheduling
            # This ensures recently-processed items don't monopolize the queue
            heapq.heappush(self._queue, (item.priority, item.run_after, item.work_id))

    async def _run_next(self) -> None:
        if not self._queue:
            return
        _, _, work_id = heapq.heappop(self._queue)
        record = self.db.get_work_item(work_id)
        if not record:
            return
        logger.debug("Running work item %s", work_id)
        self._current_work_id = work_id
        self._preempt_event.clear()
        await self._run_work_item(record)
        self._current_work_id = None
        self._preempt_event.clear()

    async def _run_work_item(self, record: WorkItemRecord) -> None:
        self.db.update_work_item_status(record.work_id, "running")
        run_id = self.db.record_run_start(record.work_id)
        self._update_state(f"running {record.work_id}")
        try:
            source = self._get_source(record.source_id)
            work_item = await source.build_work_item({"db": self.db}, record)
            logger.debug("Work item %s resume start", record.work_id)

            async def checkpoint(patch: dict[str, Any]) -> None:
                new_checkpoint = dict(record.checkpoint)
                new_checkpoint.update(patch)
                self.db.update_work_item_status(record.work_id, "running", checkpoint=new_checkpoint)

            ctx = WorkItemContext(
                db=self.db,
                executor=self.executor,
                tools=self.tools,
                should_preempt=self._preempt_event.is_set,
                checkpoint=checkpoint,
            )
            await work_item.resume(ctx)
            logger.debug("Work item %s resume completed", record.work_id)
            if self._preempt_event.is_set():
                self.db.update_work_item_status(record.work_id, "queued")
                self.db.record_run_end(run_id, "preempted")
                self._update_state(f"preempted {record.work_id}")
                return
            if record.source_id == "session":
                session_id = record.checkpoint.get("session_id")
                session = self.db.get_session(session_id) if session_id else None
                if session and session.status == "running":
                    self.db.update_work_item_status(
                        record.work_id,
                        "queued",
                        run_after=utc_now(),
                        clear_errors=True,
                    )
                    self.db.record_run_end(run_id, "queued")
                    self._update_state(f"queued {record.work_id}")
                    return
            self.db.update_work_item_status(record.work_id, "done", clear_errors=True)
            self.db.record_run_end(run_id, "done")
            self._update_state(f"completed {record.work_id}")
        except WorkItemBlocked as exc:
            logger.info("Work item %s blocked: %s", record.work_id, exc.reason)
            run_after = (datetime.now(timezone.utc).timestamp() + exc.delay_seconds)
            run_after_iso = datetime.fromtimestamp(run_after, tz=timezone.utc).isoformat()
            self.db.update_work_item_status(
                record.work_id,
                "queued",
                run_after=run_after_iso,
                last_error=exc.reason,
            )
            self.db.record_run_end(run_id, "blocked", error=exc.reason)
            self._update_state(f"blocked {record.work_id}")
        except Exception as exc: # pragma: no cover - safeguard
            tb = traceback.format_exc()
            logger.error("Work item %s failed: %s", record.work_id, exc)
            attempts = record.attempts + 1
            if attempts >= self.max_attempts:
                self.db.update_work_item_status(
                    record.work_id,
                    "failed",
                    attempts=attempts,
                    last_error=str(exc),
                    last_traceback=tb,
                )
                self.db.record_run_end(run_id, "failed", error=str(exc))
                self._update_state(f"failed {record.work_id}")
                return
            delay_seconds = 2**attempts
            run_after = (datetime.now(timezone.utc).timestamp() + delay_seconds)
            run_after_iso = datetime.fromtimestamp(run_after, tz=timezone.utc).isoformat()
            self.db.update_work_item_status(
                record.work_id,
                "queued",
                attempts=attempts,
                run_after=run_after_iso,
                last_error=str(exc),
                last_traceback=tb,
            )
            self.db.record_run_end(run_id, "retrying", error=str(exc))
            self._update_state(f"retrying {record.work_id}")

    def _get_source(self, source_id: str) -> TaskSource:
        for source in self.sources:
            if source.id == source_id:
                return source
        raise KeyError(f"Unknown source: {source_id}")

    def _update_state(self, last_action: str) -> None:
        status = "stopped" if self._stop_event.is_set() else "running"
        self.db.update_supervisor_state(
            status=status,
            current_work_id=self._current_work_id,
            last_action=last_action,
            queue_depth=len(self._queue),
        )

    async def _cleanup_repo_resources(self) -> None:
        now = datetime.now(timezone.utc).timestamp()
        if self._last_repo_cleanup and now - self._last_repo_cleanup < 86400:
            return
        self._last_repo_cleanup = now
        ttl_days = int(os.environ.get("WINTERMUTE_REPO_RESOURCE_TTL_DAYS", "30"))
        cutoff = datetime.now(timezone.utc) - timedelta(days=ttl_days)
        resources = self.db.list_repo_resources_for_cleanup(cutoff.isoformat())
        if not resources:
            return
        for resource in resources:
            agent = self.db.get_agent(resource.agent_id) if resource.agent_id else None
            vm = self.db.get_vm_target(agent.vm_target_id) if agent and agent.vm_target_id else None
            if not vm:
                logger.info("Repo cleanup dropping stale resource %s", resource.id)
                self.db.delete_repo_resource(resource.id)
                continue
            spec = build_ssh_spec(vm, "")
            try:
                delete_repo_path(spec, resource.path)
            except Exception as exc:
                logger.warning("Repo cleanup failed for %s: %s", resource.path, exc)
                continue
            self.db.delete_repo_resource(resource.id)

    async def _refresh_runtime(self) -> None:
        version = (
            self.db.get_latest_credential_update(),
            self.db.get_latest_github_token_update(),
            self.db.get_latest_gitlab_token_update(),
        )
        if version != self._tools_version:
            self.tools = build_default_tools(self.db)
            self._tools_version = version
        slack_source = self._get_slack_source()
        if slack_source:
            bot = self.db.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
            app = self.db.get_credential_by_name(SLACK_PROVIDER, SLACK_APP_TOKEN_NAME)
            signature = (
                bot.reference if bot else None,
                app.reference if app else None,
            )
            if signature != self._slack_signature:
                await slack_source.reset_socket()
                self._slack_signature = signature

    async def _cycle_sprints(self) -> None:
        """Check for expired sprints with auto-cycle enabled and create new ones."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        sprints = self.db.list_sprints(status="active")
        for sprint in sprints:
            if not sprint.enabled:
                continue
            if sprint.end_date >= today:
                continue
            # Sprint has ended and auto-cycle is enabled
            try:
                start = datetime.strptime(sprint.start_date, "%Y-%m-%d")
                end = datetime.strptime(sprint.end_date, "%Y-%m-%d")
                duration = end - start
            except ValueError:
                logger.warning("Sprint %s has invalid dates, skipping cycle", sprint.id)
                continue
            # Create new sprint starting day after old one ended
            new_start = end + timedelta(days=1)
            new_end = new_start + duration
            # Generate new sprint name (increment number if present)
            match = re.search(r"(\d+)$", sprint.name)
            if match:
                num = int(match.group(1)) + 1
                new_name = sprint.name[:match.start()] + str(num)
            else:
                new_name = sprint.name + " 2"
            new_sprint_id = str(uuid.uuid4())
            self.db.insert_sprint(
                sprint_id=new_sprint_id,
                name=new_name,
                start_date=new_start.strftime("%Y-%m-%d"),
                end_date=new_end.strftime("%Y-%m-%d"),
                enabled=True,
                status="active",
            )
            # Move open tickets to new sprint
            moved = self.db.move_open_tickets_to_sprint(sprint.id, new_sprint_id)
            # Close old sprint
            self.db.update_sprint(sprint.id, status="closed")
            logger.info(
                "Cycled sprint %s -> %s, moved %d tickets",
                sprint.name,
                new_name,
                moved,
            )

    def _get_slack_source(self) -> Optional[SlackSource]:
        for source in self.sources:
            if isinstance(source, SlackSource):
                return source
        return None


def build_default_tools(db: Optional[Database] = None) -> ToolRegistry:
    registry = ToolRegistry()
    allowlist = _parse_allowlist(os.environ.get("WINTERMUTE_FS_ALLOWLIST", ""))
    if allowlist:
        registry.register(ReadFileTool(allowlist=allowlist))
    if db:
        slack_bot = db.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        if slack_bot:
            registry.register(SlackPostMessageTool(token=slack_bot.reference))
        if db.list_github_tokens():
            registry.register(GitHubListIssuesTool(db=db))
            registry.register(GitHubGetIssueTool(db=db))
            registry.register(GitHubCommentIssueTool(db=db))
        if db.list_gitlab_tokens():
            registry.register(GitLabListIssuesTool(db=db))
            registry.register(GitLabGetIssueTool(db=db))
            registry.register(GitLabCommentIssueTool(db=db))
    return registry


async def main() -> None:
    log_level = os.environ.get("WINTERMUTE_LOG_LEVEL", "INFO").upper()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_path = os.environ.get("WINTERMUTE_SUPERVISOR_LOG_FILE")
    if log_path:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handlers.append(logging.FileHandler(log_path))
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    register(DemoSource())
    register(CommentDispatchSource())
    register(GitHubIssuesSource())
    register(GitLabIssuesSource())
    register(SlackSource())
    register(SessionSource())
    register(TicketAutoStartSource())
    register(StandupSource())
    db = Database()

    # Reset any work items stuck in "running" state from previous crash/kill
    stuck_items = db.list_work_items(status="running")
    if stuck_items:
        logger = logging.getLogger(__name__)
        logger.info("Resetting %d stuck work items from previous run", len(stuck_items))
        for item in stuck_items:
            db.update_work_item_status(item.work_id, "queued")
            logger.info("Reset stuck work item: %s", item.work_id)

    executor = Executor()
    tools = build_default_tools(db)
    supervisor = Supervisor(db=db, sources=all_sources(), executor=executor, tools=tools)
    print(f"Wintermute supervisor v{__version__} starting...")
    print("Supervisor ready. Polling sources and processing tasks.")
    await supervisor.run()


if __name__ == "__main__":
    asyncio.run(main())
