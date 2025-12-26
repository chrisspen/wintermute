"""Slack tools for posting messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from slack_sdk.web.async_client import AsyncWebClient

from wintermute.tools.base import Tool, ToolDefinition


@dataclass
class SlackPostMessageTool(Tool):
    token: str

    def __post_init__(self) -> None:
        self._client = AsyncWebClient(token=self.token)

    definition: ToolDefinition = ToolDefinition(
        name="slack_post_message",
        description="Post a message to Slack, optionally as a thread reply.",
        input_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "text": {"type": "string"},
                "thread_ts": {"type": "string"},
            },
            "required": ["channel", "text"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        channel = args.get("channel")
        text = args.get("text")
        thread_ts: Optional[str] = args.get("thread_ts")
        if not channel or not text:
            raise ValueError("channel and text are required")
        response = await self._client.chat_postMessage(
            channel=channel,
            text=text,
            thread_ts=thread_ts,
        )
        return {
            "ok": response.get("ok"),
            "channel": response.get("channel"),
            "ts": response.get("ts"),
        }
