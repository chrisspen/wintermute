"""Filesystem tools with allowlisted paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from wintermute.tools.base import Tool, ToolDefinition


def _allowlisted(path: str, allowlist: list[str]) -> bool:
    abs_path = os.path.abspath(path)
    for root in allowlist:
        if abs_path.startswith(os.path.abspath(root) + os.sep):
            return True
    return False


@dataclass
class ReadFileTool(Tool):
    allowlist: list[str]
    definition: ToolDefinition = ToolDefinition(
        name="fs_read",
        description="Read a text file from an allowlisted path.",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        path = args.get("path", "")
        if not _allowlisted(path, self.allowlist):
            raise PermissionError("Path not allowlisted")
        with open(path, "r", encoding="utf-8") as handle:
            return {"path": path, "content": handle.read()}
