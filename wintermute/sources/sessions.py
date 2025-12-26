"""Agent session output polling source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from wintermute.db import Database
from wintermute.runner import build_ssh_spec, read_output
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft


@dataclass
class SessionWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    session_id: str

    async def resume(self, ctx: WorkItemContext) -> None:
        session = ctx.db.get_session(self.session_id)
        if not session:
            return
        if session.status != "running":
            return
        project = ctx.db.get_project(session.project_id)
        project_vm = ctx.db.get_project_vm(session.project_vm_id)
        agent = ctx.db.get_agent(session.agent_id)
        vm = ctx.db.get_vm_target(project_vm.vm_target_id) if project_vm else None
        if not (project and project_vm and agent and vm):
            return
        if not project.slack_channel_id or not session.thread_ts:
            return
        spec = build_ssh_spec(vm, agent.required_ssh_options)
        output, new_offset = read_output(spec, session)
        if not output:
            return
        ctx.db.update_session(session.id, last_output=output, last_output_offset=new_offset)
        prefix = f"[{agent.slug}] "
        chunks = _chunk_text(output, 3000)
        for chunk in chunks:
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": project.slack_channel_id,
                    "thread_ts": session.thread_ts,
                    "text": prefix + chunk,
                },
            )


def _chunk_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size
    return chunks


class SessionSource(TaskSource):
    id = "session"
    enabled = True
    base_priority = 90
    poll_interval_seconds = 2

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        sessions = db.list_sessions(status="running")
        drafts: list[WorkItemDraft] = []
        for session in sessions:
            drafts.append(
                WorkItemDraft(
                    work_id=f"session:{session.id}",
                    priority=self.base_priority,
                    source_id=self.id,
                    checkpoint={"session_id": session.id},
                )
            )
        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        session_id = record.checkpoint["session_id"]
        return SessionWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            session_id=session_id,
        )
