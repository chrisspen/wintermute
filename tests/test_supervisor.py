"""Tests for Supervisor functionality."""

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from asgiref.sync import async_to_sync

from wintermute.models import TaskSource, WorkItem
from wintermute.executor import Executor
from wintermute.sources.base import TaskSource as TaskSourceBase, WorkItem as WorkItemBase, WorkItemDraft
from wintermute.supervisor import Supervisor
from wintermute.tools.base import ToolRegistry
from wintermute.utils import utc_now


@dataclass
class NoopWorkItem(WorkItemBase):
    work_id: str
    priority: int
    source_id: str

    async def resume(self, ctx: Any) -> None:
        return None


@dataclass
class FailingWorkItem(WorkItemBase):
    work_id: str
    priority: int
    source_id: str

    async def resume(self, ctx: Any) -> None:
        raise RuntimeError("boom")


class QueueSource(TaskSourceBase):

    def __init__(self, drafts: list[WorkItemDraft]) -> None:
        self.id = "queue"
        self.enabled = True
        self.base_priority = 10
        self.poll_interval_seconds = 0
        self._drafts = drafts

    async def poll(self, ctx: Any) -> list[WorkItemDraft]:
        return list(self._drafts)

    async def build_work_item(self, ctx: Any, record: Any) -> WorkItemBase:
        return NoopWorkItem(record.work_id, record.priority, record.source_id)


class FailingSource(QueueSource):

    async def build_work_item(self, ctx: Any, record: Any) -> WorkItemBase:
        return FailingWorkItem(record.work_id, record.priority, record.source_id)


class MockAsyncDatabase:
    """Mock async database for testing."""

    def __init__(self):
        self._work_items = {}
        self._task_sources = {}

    async def get_task_source(self, source_id: str):
        return self._task_sources.get(source_id)

    async def list_task_sources(self):
        return list(self._task_sources.values())

    async def upsert_task_source(self, source_id, enabled, base_priority, poll_interval_seconds, config):
        now = utc_now()
        self._task_sources[source_id] = type(
            'TaskSource', (), {
                'id': source_id,
                'enabled': enabled,
                'base_priority': base_priority,
                'poll_interval_seconds': poll_interval_seconds,
            }
        )()
        return self._task_sources[source_id]

    async def insert_work_item_if_absent(self, work_id, source_id, priority, checkpoint):
        if work_id in self._work_items:
            return False
        now = utc_now()
        self._work_items[work_id] = type(
            'WorkItem', (), {
                'work_id': work_id,
                'source_id': source_id,
                'priority': priority,
                'status': 'queued',
                'checkpoint_json': str(checkpoint),
                'run_after': '',
                'attempts': 0,
                'created_at': now,
                'updated_at': now,
            }
        )()
        return True

    async def get_work_item(self, work_id):
        return self._work_items.get(work_id)

    async def fetch_ready_work_items(self, now_iso):
        return [w for w in self._work_items.values() if w.status == 'queued']

    async def update_work_item_status(self, work_id, status, **kwargs):
        if work_id in self._work_items:
            self._work_items[work_id].status = status
            for key, value in kwargs.items():
                if hasattr(self._work_items[work_id], key):
                    setattr(self._work_items[work_id], key, value)
            return True
        return False

    async def record_run_start(self, work_id):
        return 1

    async def record_run_end(self, run_id, status, error=None):
        pass

    async def upsert_supervisor_state(self, status, current_work_id=None, last_action=None, queue_depth=0):
        return type('SupervisorState', (), {
            'status': status,
            'current_work_id': current_work_id,
            'last_action': last_action,
            'queue_depth': queue_depth,
        })()

    async def update_supervisor_state(self, status=None, current_work_id=None, last_action=None, queue_depth=None):
        return await self.upsert_supervisor_state(status, current_work_id, last_action, queue_depth or 0)


@pytest.mark.django_db
class TestSupervisor:
    """Tests for Supervisor."""

    def test_dedupes_work_items(self):
        """Supervisor should dedupe work items by work_id."""

        async def run_test():
            db = MockAsyncDatabase()
            tools = ToolRegistry()
            executor = Executor(base_url="http://localhost:11434/v1", api_key="test", model="test")

            draft = WorkItemDraft(work_id="a", priority=5, source_id="queue", checkpoint={})
            source = QueueSource([draft])
            supervisor = Supervisor(db, [source], executor, tools)

            await supervisor._ensure_task_sources()
            await supervisor._poll_sources()
            await supervisor._poll_sources()

            items = await db.fetch_ready_work_items("9999-01-01T00:00:00+00:00")
            assert len(items) == 1

        async_to_sync(run_test)()

    def test_retry_backoff(self):
        """Supervisor should track attempts on failed work items."""

        async def run_test():
            db = MockAsyncDatabase()
            tools = ToolRegistry()
            executor = Executor(base_url="http://localhost:11434/v1", api_key="test", model="test")

            draft = WorkItemDraft(work_id="b", priority=5, source_id="queue", checkpoint={})
            source = FailingSource([draft])
            supervisor = Supervisor(db, [source], executor, tools, max_attempts=1)

            await supervisor._ensure_task_sources()
            await db.insert_work_item_if_absent(draft.work_id, draft.source_id, draft.priority, draft.checkpoint)

            record = await db.get_work_item("b")
            assert record is not None

            await supervisor._run_work_item(record)

            record = await db.get_work_item("b")
            assert record.status == "failed"

        async_to_sync(run_test)()

    def test_preemption_flagged(self):
        """Supervisor should set preempt event when higher priority work arrives."""

        async def run_test():
            db = MockAsyncDatabase()
            tools = ToolRegistry()
            executor = Executor(base_url="http://localhost:11434/v1", api_key="test", model="test")

            high = WorkItemDraft(work_id="high", priority=1, source_id="queue", checkpoint={})
            source = QueueSource([high])
            supervisor = Supervisor(db, [source], executor, tools)

            await supervisor._ensure_task_sources()
            await db.insert_work_item_if_absent("low", "queue", 10, {})
            supervisor._current_work_id = "low"

            await supervisor._poll_sources()

            assert supervisor._preempt_event.is_set()

        async_to_sync(run_test)()
