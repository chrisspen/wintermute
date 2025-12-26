"""TaskSource and WorkItem definitions."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class WorkItemDraft:
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]


class WorkItem(Protocol):
    work_id: str
    priority: int
    source_id: str

    async def resume(self, ctx: "WorkItemContext") -> None:
        ...


@dataclass
class WorkItemContext:
    db: Any
    executor: Any
    tools: Any
    should_preempt: Any
    checkpoint: Any


class TaskSource(abc.ABC):
    id: str
    enabled: bool
    base_priority: int
    poll_interval_seconds: int

    @abc.abstractmethod
    async def poll(self, ctx: Any) -> list[WorkItemDraft]:
        raise NotImplementedError

    @abc.abstractmethod
    async def build_work_item(self, ctx: Any, record: Any) -> WorkItem:
        raise NotImplementedError
