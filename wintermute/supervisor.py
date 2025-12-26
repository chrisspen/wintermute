"""Supervisor loop for scheduling and preempting work items."""

from __future__ import annotations

import asyncio
import heapq
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from wintermute import __version__
from wintermute.db import Database, WorkItemRecord, utc_now
from wintermute.executor import Executor
from wintermute.sources.base import TaskSource, WorkItemContext
from wintermute.sources.demo import DemoSource
from wintermute.sources.slack import SlackSource, SLACK_BOT_TOKEN_NAME, SLACK_PROVIDER
from wintermute.sources.registry import all_sources, register
from wintermute.tools.base import ToolRegistry
from wintermute.tools.fs import ReadFileTool
from wintermute.tools.slack import SlackPostMessageTool


def _parse_allowlist(value: str) -> list[str]:
    return [entry.strip() for entry in value.split(",") if entry.strip()]


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
                await self._poll_sources()
                self._update_state("polled sources")
                await self._refresh_queue()
                self._update_state("refreshed queue")
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
            if not row or not row.enabled:
                continue
            last_poll = self._last_poll.get(source.id, 0.0)
            if now - last_poll < row.poll_interval_seconds:
                continue
            self._last_poll[source.id] = now
            drafts = await source.poll({"db": self.db})
            for draft in drafts:
                inserted = self.db.insert_work_item_if_absent(
                    draft.work_id,
                    draft.source_id,
                    draft.priority,
                    draft.checkpoint,
                )
                if inserted:
                    if self._current_work_id:
                        current = self.db.get_work_item(self._current_work_id)
                        if current and draft.priority < current.priority:
                            self._preempt_event.set()

    async def _refresh_queue(self) -> None:
        self._queue.clear()
        for item in self.db.fetch_ready_work_items(utc_now()):
            if item.work_id == self._current_work_id:
                continue
            heapq.heappush(self._queue, (item.priority, item.created_at, item.work_id))

    async def _run_next(self) -> None:
        if not self._queue:
            return
        _, _, work_id = heapq.heappop(self._queue)
        record = self.db.get_work_item(work_id)
        if not record:
            return
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
            if self._preempt_event.is_set():
                self.db.update_work_item_status(record.work_id, "queued")
                self.db.record_run_end(run_id, "preempted")
                self._update_state(f"preempted {record.work_id}")
                return
            self.db.update_work_item_status(record.work_id, "done")
            self.db.record_run_end(run_id, "done")
            self._update_state(f"completed {record.work_id}")
        except Exception as exc:  # pragma: no cover - safeguard
            attempts = record.attempts + 1
            if attempts >= self.max_attempts:
                self.db.update_work_item_status(
                    record.work_id,
                    "failed",
                    attempts=attempts,
                    last_error=str(exc),
                )
                self.db.record_run_end(run_id, "failed", error=str(exc))
                self._update_state(f"failed {record.work_id}")
                return
            delay_seconds = 2 ** attempts
            run_after = (
                datetime.now(timezone.utc).timestamp() + delay_seconds
            )
            run_after_iso = datetime.fromtimestamp(run_after, tz=timezone.utc).isoformat()
            self.db.update_work_item_status(
                record.work_id,
                "queued",
                attempts=attempts,
                run_after=run_after_iso,
                last_error=str(exc),
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


def build_default_tools(db: Optional[Database] = None) -> ToolRegistry:
    registry = ToolRegistry()
    allowlist = _parse_allowlist(os.environ.get("WINTERMUTE_FS_ALLOWLIST", ""))
    if allowlist:
        registry.register(ReadFileTool(allowlist=allowlist))
    if db:
        slack_bot = db.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        if slack_bot:
            registry.register(SlackPostMessageTool(token=slack_bot.reference))
    return registry


async def main() -> None:
    register(DemoSource())
    register(SlackSource())
    db = Database()
    executor = Executor()
    tools = build_default_tools(db)
    supervisor = Supervisor(db=db, sources=all_sources(), executor=executor, tools=tools)
    print(f"Foreman supervisor v{__version__} starting...")
    await supervisor.run()


if __name__ == "__main__":
    asyncio.run(main())
