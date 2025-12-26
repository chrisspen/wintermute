"""Slack TaskSource using Socket Mode."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from wintermute.db import Database, utc_now
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft


SLACK_PROVIDER = "slack"
SLACK_BOT_TOKEN_NAME = "bot_token"
SLACK_APP_TOKEN_NAME = "app_token"


@dataclass
class SlackMessage:
    event_id: str
    channel: str
    user: str
    text: str
    ts: str
    thread_ts: str


@dataclass
class SlackWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    message: SlackMessage

    async def resume(self, ctx: WorkItemContext) -> None:
        state = {
            "work_id": self.work_id,
            "source": "slack",
            "channel": self.message.channel,
            "thread_ts": self.message.thread_ts,
        }
        observation: dict[str, Any] = {
            "event": {
                "channel": self.message.channel,
                "user": self.message.user,
                "text": self.message.text,
                "ts": self.message.ts,
                "thread_ts": self.message.thread_ts,
            },
            "instruction": (
                "If responding in Slack, use the slack_post_message tool. "
                "Prefer replying in thread using thread_ts."
            ),
        }
        tool_schema = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in ctx.tools.definitions()
        ]
        for _ in range(5):
            if ctx.should_preempt():
                return
            decision = ctx.executor.decide_next_action(state, observation, tool_schema)
            if decision.type == "tool":
                result = await ctx.tools.call(decision.payload["name"], decision.payload["args"])
                observation = {"tool_result": result, "tool_name": decision.payload["name"]}
                continue
            if decision.type == "update":
                await ctx.checkpoint(decision.payload["patch"])
                observation = {"checkpoint_updated": decision.payload["patch"]}
                continue
            if decision.type == "escalate":
                ctx.db.update_work_item_status(
                    self.work_id, "queued", priority=int(decision.payload["priority"])
                )
                return
            if decision.type == "yield":
                ctx.db.update_work_item_status(self.work_id, "queued", run_after=utc_now())
                return
            if decision.type == "done":
                return


class SlackSource(TaskSource):
    id = "slack"
    enabled = False
    base_priority = 50
    poll_interval_seconds = 2

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SlackMessage] = asyncio.Queue()
        self._socket_task: Optional[asyncio.Task[None]] = None
        self._client: Optional[SocketModeClient] = None
        self._bot_user_id: Optional[str] = None
        self._channels_filter: Optional[set[str]] = None

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        source = db.get_task_source(self.id)
        if not source or not source.enabled:
            return []
        self._channels_filter = self._normalize_channels(source.config.get("channels"))
        try:
            await self._ensure_socket(db, source.config)
        except Exception:
            return []
        drafts: list[WorkItemDraft] = []
        while True:
            try:
                message = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            work_id = f"slack:{message.event_id}"
            drafts.append(
                WorkItemDraft(
                    work_id=work_id,
                    priority=source.base_priority,
                    source_id=self.id,
                    checkpoint={
                        "event_id": message.event_id,
                        "channel": message.channel,
                        "user": message.user,
                        "text": message.text,
                        "ts": message.ts,
                        "thread_ts": message.thread_ts,
                    },
                )
            )
        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        checkpoint = record.checkpoint
        message = SlackMessage(
            event_id=checkpoint["event_id"],
            channel=checkpoint["channel"],
            user=checkpoint["user"],
            text=checkpoint["text"],
            ts=checkpoint["ts"],
            thread_ts=checkpoint["thread_ts"],
        )
        return SlackWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            message=message,
        )

    async def _ensure_socket(self, db: Database, config: dict[str, Any]) -> None:
        if self._socket_task:
            return
        bot_token = db.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        app_token = db.get_credential_by_name(SLACK_PROVIDER, SLACK_APP_TOKEN_NAME)
        if not bot_token or not app_token:
            return
        web_client = AsyncWebClient(token=bot_token.reference)
        auth = await web_client.auth_test()
        self._bot_user_id = auth.get("user_id")
        client = SocketModeClient(app_token=app_token.reference, web_client=web_client)
        client.socket_mode_request_listeners.append(self._handle_socket_request)
        self._client = client
        self._socket_task = asyncio.create_task(self._socket_loop(client, config))

    async def _socket_loop(self, client: SocketModeClient, config: dict[str, Any]) -> None:
        await client.connect()
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await client.disconnect()

    async def _handle_socket_request(self, client: SocketModeClient, req: Any) -> None:
        if req.type != "events_api":
            return
        await client.send_socket_mode_response(SocketModeResponse(envelope_id=req.envelope_id))
        payload = req.payload or {}
        event = payload.get("event") or {}
        if event.get("type") != "message":
            return
        if event.get("subtype"):
            return
        if self._bot_user_id and event.get("user") == self._bot_user_id:
            return
        channel = event.get("channel")
        if not channel:
            return
        if self._channels_filter and channel not in self._channels_filter:
            return
        message = SlackMessage(
            event_id=payload.get("event_id", f"{channel}:{event.get('ts')}"),
            channel=channel,
            user=event.get("user", ""),
            text=event.get("text", ""),
            ts=event.get("ts", ""),
            thread_ts=event.get("thread_ts") or event.get("ts", ""),
        )
        await self._queue.put(message)

    @staticmethod
    def _normalize_channels(raw: Any) -> Optional[set[str]]:
        if not raw:
            return None
        if isinstance(raw, list):
            return {str(item).strip() for item in raw if str(item).strip()}
        channels = []
        for item in str(raw).replace("\n", ",").split(","):
            cleaned = item.strip()
            if cleaned:
                channels.append(cleaned)
        return set(channels) or None
