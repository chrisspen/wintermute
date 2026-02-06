import os
import tempfile
import unittest
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync

from wintermute.db import AsyncDatabase, Database
from wintermute.executor import Executor
from wintermute.sources.base import TaskSource, WorkItem, WorkItemDraft
from wintermute.supervisor import Supervisor
from wintermute.tools.base import ToolRegistry


@dataclass
class NoopWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str

    async def resume(self, ctx: Any) -> None:
        return None


@dataclass
class FailingWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str

    async def resume(self, ctx: Any) -> None:
        raise RuntimeError("boom")


class QueueSource(TaskSource):

    def __init__(self, drafts: list[WorkItemDraft]) -> None:
        self.id = "queue"
        self.enabled = True
        self.base_priority = 10
        self.poll_interval_seconds = 0
        self._drafts = drafts

    async def poll(self, ctx: Any) -> list[WorkItemDraft]:
        return list(self._drafts)

    async def build_work_item(self, ctx: Any, record: Any) -> WorkItem:
        return NoopWorkItem(record.work_id, record.priority, record.source_id)


class FailingSource(QueueSource):

    async def build_work_item(self, ctx: Any, record: Any) -> WorkItem:
        return FailingWorkItem(record.work_id, record.priority, record.source_id)


class SupervisorTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_sync = Database(self.temp_db.name)
        self.db_sync.initialize()
        self.db = AsyncDatabase(self.temp_db.name)
        self.tools = ToolRegistry()
        self.executor = Executor(base_url="http://localhost:11434/v1", api_key="test", model="test")

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_dedupes_work_items(self) -> None:

        async def run_test():
            draft = WorkItemDraft(work_id="a", priority=5, source_id="queue", checkpoint={})
            source = QueueSource([draft])
            supervisor = Supervisor(self.db, [source], self.executor, self.tools)
            await supervisor._ensure_task_sources()
            await supervisor._poll_sources()
            await supervisor._poll_sources()
            items = await self.db.fetch_ready_work_items("9999-01-01T00:00:00+00:00")
            self.assertEqual(len(items), 1)

        async_to_sync(run_test)()

    def test_retry_backoff(self) -> None:

        async def run_test():
            draft = WorkItemDraft(work_id="b", priority=5, source_id="queue", checkpoint={})
            source = FailingSource([draft])
            supervisor = Supervisor(self.db, [source], self.executor, self.tools, max_attempts=1)
            await supervisor._ensure_task_sources()
            await self.db.insert_work_item_if_absent(draft.work_id, draft.source_id, draft.priority, draft.checkpoint)
            record = await self.db.get_work_item("b")
            assert record is not None
            await supervisor._run_work_item(record)
            record = await self.db.get_work_item("b")
            self.assertEqual(record.status, "failed")
            self.assertEqual(record.attempts, 1)

        async_to_sync(run_test)()

    def test_preemption_flagged(self) -> None:

        async def run_test():
            low = WorkItemDraft(work_id="low", priority=10, source_id="queue", checkpoint={})
            high = WorkItemDraft(work_id="high", priority=1, source_id="queue", checkpoint={})
            source = QueueSource([high])
            supervisor = Supervisor(self.db, [source], self.executor, self.tools)
            await supervisor._ensure_task_sources()
            await self.db.insert_work_item_if_absent(low.work_id, low.source_id, low.priority, low.checkpoint)
            supervisor._current_work_id = "low"
            await supervisor._poll_sources()
            self.assertTrue(supervisor._preempt_event.is_set())

        async_to_sync(run_test)()


if __name__ == "__main__":
    unittest.main()
