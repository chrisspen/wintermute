"""Registry for TaskSources."""

from __future__ import annotations

from typing import Dict, Iterable

from wintermute.sources.base import TaskSource

_REGISTRY: Dict[str, TaskSource] = {}


def register(source: TaskSource) -> None:
    _REGISTRY[source.id] = source


def get(source_id: str) -> TaskSource:
    return _REGISTRY[source_id]


def all_sources() -> Iterable[TaskSource]:
    return list(_REGISTRY.values())
