"""Tool definitions and registry."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]


class Tool(abc.ABC):
    definition: ToolDefinition

    @abc.abstractmethod
    async def __call__(self, args: dict[str, Any]) -> Any:
        raise NotImplementedError


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.name] = tool

    def definitions(self) -> Iterable[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Tool not found: {name}")
        return await tool(args)

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)
