"""Agent session output polling source."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional
import logging

import uuid

from wintermute.db import Database, AgentSessionRecord
from wintermute.runner import build_ssh_spec, is_session_running, read_output
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft


@dataclass
class SessionWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    session_id: str

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
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
        if not is_session_running(spec, session.id):
            ctx.db.update_session(session.id, status="done")
            if ctx.tools.get("slack_post_message"):
                await ctx.tools.call(
                    "slack_post_message",
                    {
                        "channel": project.slack_channel_id,
                        "thread_ts": session.thread_ts,
                        "text": f"[{agent.slug}] session ended.",
                    },
                )
            return
        output, new_offset = read_output(spec, session)
        if not output:
            return
        logger.info("Session %s produced %d bytes", session.id, len(output))
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
        await _handle_session_markers(ctx, session, output)


async def _handle_session_markers(
    ctx: WorkItemContext, session: AgentSessionRecord, output: str
) -> None:
    ticket_id = session.ticket_id
    if not ticket_id:
        return
    public_lines, note_lines = _extract_marked_lines(output)
    source_id = _parse_github_source_id(ticket_id)
    issue_number = _parse_github_issue_number(ticket_id)
    for line in public_lines:
        ctx.db.insert_comment(
            comment_id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            session_id=session.id,
            project_id=session.project_id,
            agent_id=session.agent_id,
            source_id=source_id,
            issue_number=issue_number,
            body=line,
            public=True,
            approved=False,
        )
    for line in note_lines:
        ctx.db.insert_comment(
            comment_id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            session_id=session.id,
            project_id=session.project_id,
            agent_id=session.agent_id,
            source_id=source_id,
            issue_number=issue_number,
            body=line,
            public=False,
            approved=False,
        )


def _extract_marked_lines(output: str) -> tuple[list[str], list[str]]:
    public_lines: list[str] = []
    note_lines: list[str] = []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        if upper.startswith("PUBLIC:") or upper.startswith("GITHUB:"):
            public_lines.append(line.split(":", 1)[1].strip())
            continue
        if upper.startswith("NOTE:"):
            note_lines.append(line.split(":", 1)[1].strip())
            continue
    return public_lines, note_lines


def _parse_github_source_id(ticket_id: str) -> Optional[str]:
    if not ticket_id.startswith("github:"):
        return None
    parts = ticket_id.split(":")
    if len(parts) < 3:
        return None
    return parts[1] or None


def _parse_github_issue_number(ticket_id: str) -> Optional[int]:
    if not ticket_id.startswith("github:"):
        return None
    parts = ticket_id.split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None




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
