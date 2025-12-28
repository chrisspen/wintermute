"""Tool definitions and registry."""

from __future__ import annotations

import abc
import json
import logging
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
        self._logger = logging.getLogger(__name__)

    def register(self, tool: Tool) -> None:
        self._tools[tool.definition.name] = tool

    def definitions(self) -> Iterable[ToolDefinition]:
        return [tool.definition for tool in self._tools.values()]

    async def call(self, name: str, args: dict[str, Any]) -> Any:
        tool = self._tools.get(name)
        if not tool:
            raise ValueError(f"Tool not found: {name}")
        safe_args = self._sanitize(args)
        self._logger.info("Tool call %s args=%s", name, safe_args)
        result = await tool(args)
        self._logger.info("Tool result %s summary=%s", name, self._summarize(result))
        return result

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def _sanitize(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            sanitized = {}
            for key, value in payload.items():
                lowered = str(key).lower()
                if any(token in lowered for token in ("token", "secret", "password", "authorization", "api_key")):
                    sanitized[key] = "***"
                else:
                    sanitized[key] = self._sanitize(value)
            return sanitized
        if isinstance(payload, list):
            return [self._sanitize(item) for item in payload]
        return payload

    def _summarize(self, payload: Any) -> str:
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        else:
            text = str(payload)
        if len(text) > 300:
            return text[:300] + "..."
        return text
