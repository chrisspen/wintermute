"""Slack TaskSource using Socket Mode."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional
import logging

from slack_sdk.socket_mode.aiohttp import SocketModeClient
from slack_sdk.socket_mode.response import SocketModeResponse
from slack_sdk.web.async_client import AsyncWebClient

from wintermute.db import Database
from wintermute.runner import build_ssh_spec, ensure_repo, is_codex_command, send_input, set_codex_trust, start_session
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
class SlackCommandWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    message: SlackMessage

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
        if self.message.thread_ts and self.message.thread_ts != self.message.ts:
            session = ctx.db.get_session_by_thread(self.message.thread_ts)
            if session:
                project_vm = ctx.db.get_project_vm(session.project_vm_id)
                agent = ctx.db.get_agent(session.agent_id)
                vm = ctx.db.get_vm_target(project_vm.vm_target_id) if project_vm else None
                if project_vm and agent and vm:
                    spec = build_ssh_spec(vm, agent.required_ssh_options)
                    send_input(spec, session, self.message.text)
            return

        text = (self.message.text or "").strip()
        if not text.lower().startswith("start "):
            return

        parts = text.split()
        if len(parts) < 3:
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": "Usage: start <projectslug> <agentslug>",
                },
            )
            return

        project_slug = parts[1].lower()
        agent_slug = parts[2].lower()
        project = ctx.db.get_project_by_slug(project_slug)
        if not project:
            logger.info("Slack start failed: project %s not found", project_slug)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": f"Project not found: {project_slug}",
                },
            )
            return
        if project.slack_channel_id != self.message.channel:
            logger.info("Slack start blocked: channel mismatch for %s", project_slug)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": "This channel is not linked to that project.",
                },
            )
            return
        agent = ctx.db.get_agent_by_slug(agent_slug)
        if not agent:
            logger.info("Slack start failed: agent %s not found", agent_slug)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": f"Agent not found: {agent_slug}",
                },
            )
            return
        project_vm = ctx.db.get_project_vm_for_project(project.id)
        if not project_vm:
            logger.info("Slack start failed: no VM mapping for project %s", project_slug)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": "No VM configured for this project.",
                },
            )
            return
        if project_vm.repo_mode == "mirror":
            existing = ctx.db.list_sessions(project_id=project.id, status="running")
            for session in existing:
                if session.project_vm_id == project_vm.id:
                    logger.info("Slack start blocked: session already running for project %s", project_slug)
                    await ctx.tools.call(
                        "slack_post_message",
                        {
                            "channel": self.message.channel,
                            "thread_ts": self.message.ts,
                            "text": "A session is already running for this project/VM.",
                        },
                    )
                    return
        vm = ctx.db.get_vm_target(project_vm.vm_target_id)
        if not vm:
            logger.info("Slack start failed: VM target missing for project %s", project_slug)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": "VM target not found.",
                },
            )
            return

        spec = build_ssh_spec(vm, agent.required_ssh_options)
        session_id = f"{project_slug}-{agent_slug}-{self.message.ts.replace('.', '')}"
        repo_resource, resource_error = ctx.db.acquire_repo_resource(
            project=project,
            project_vm=project_vm,
            session_id=session_id,
            agent_id=agent.id,
        )
        if not repo_resource:
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": resource_error or "Repo resource unavailable.",
                },
            )
            return
        try:
            repo_path = ensure_repo(spec, project_vm, repo_path=repo_resource.path)
        except Exception as exc:
            ctx.db.release_repo_resource_for_session(session_id)
            logger.info("Slack start failed: repo setup error %s", exc)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": f"Repo setup failed: {exc}",
                },
            )
            return
        if not repo_path:
            ctx.db.release_repo_resource_for_session(session_id)
            logger.info("Slack start failed: repo not configured for project %s", project_slug)
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": self.message.channel,
                    "thread_ts": self.message.ts,
                    "text": "Repository not configured for this project VM.",
                },
            )
            return
        if is_codex_command(agent.command) and agent.trust_level:
            set_codex_trust(spec, repo_path, agent.trust_level)
        ctx.db.insert_session(
            session_id=session_id,
            project_id=project.id,
            project_vm_id=project_vm.id,
            agent_id=agent.id,
            ticket_id=None,
            status="running",
            repo_path=repo_path,
            thread_ts=self.message.ts,
        )
        logger.info("Slack start session %s for project %s", session_id, project_slug)
        start_session(spec, session_id, agent, repo_path)
        await ctx.tools.call(
            "slack_post_message",
            {
                "channel": self.message.channel,
                "thread_ts": self.message.ts,
                "text": f"[{agent.slug}] session started in {repo_path}",
            },
        )


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

    async def reset_socket(self) -> None:
        if self._socket_task:
            self._socket_task.cancel()
            try:
                await self._socket_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass
            self._socket_task = None
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self._bot_user_id = None

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        source = db.get_task_source(self.id)
        if not source or not source.enabled:
            return []
        logger = logging.getLogger(__name__)
        self._channels_filter = self._normalize_channels(source.config.get("channels"))
        try:
            await self._ensure_socket(db, source.config)
        except Exception:
            logger.exception("Slack socket initialization failed")
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
        if drafts:
            logger.info("Slack poll yielded %d messages", len(drafts))
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
        return SlackCommandWorkItem(
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
