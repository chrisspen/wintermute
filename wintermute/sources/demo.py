"""Demo TaskSource that emits no work."""

from __future__ import annotations

from typing import Any

from wintermute.sources.base import TaskSource, WorkItem, WorkItemDraft


class DemoSource(TaskSource):
    id = "demo"
    enabled = True
    base_priority = 100
    poll_interval_seconds = 30

    async def poll(self, ctx: Any) -> list[WorkItemDraft]:
        return []

    async def build_work_item(self, ctx: Any, record: Any) -> WorkItem:
        raise NotImplementedError("DemoSource does not create work items")
