"""Agent session output polling source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
import logging
import re

import uuid

from wintermute.db import Database, AgentSessionRecord, utc_now
from wintermute.runner import build_ssh_spec, send_input, is_session_running, read_output
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
        spec = build_ssh_spec(vm, agent.required_ssh_options)
        if not is_session_running(spec, session.id):
            ctx.db.update_session(session.id, status="done")
            if project.slack_channel_id and session.thread_ts and ctx.tools.get("slack_post_message"):
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
        buffer_text = session.output_buffer or ""
        buffer_updated_at = session.output_buffer_updated_at
        if output:
            logger.info("Session %s produced %d bytes", session.id, len(output))
            ctx.db.update_session(
                session.id,
                last_output=output,
                last_output_offset=new_offset,
                output_buffer=buffer_text + output,
                output_buffer_updated_at=utc_now(),
                last_output_at=utc_now(),
            )
            await _maybe_send_prompt(ctx, session, spec, buffer_text + output)
            return
        if buffer_text and _should_flush_buffer(buffer_updated_at, seconds=4):
            cleaned_buffer = _strip_echo_lines(buffer_text, agent.input_echo_prefix)
            await _emit_output(ctx, session, project, agent, cleaned_buffer)
            await _apply_agent_responses(ctx, session, spec, cleaned_buffer)
            await _handle_session_markers(ctx, session, cleaned_buffer)
            ctx.db.update_session(
                session.id,
                output_buffer="",
                output_buffer_updated_at=utc_now(),
            )
        await _maybe_send_prompt(ctx, session, spec, buffer_text)
        return


async def _handle_session_markers(
    ctx: WorkItemContext, session: AgentSessionRecord, output: str
) -> None:
    ticket_id = session.ticket_id
    if not ticket_id:
        return
    public_lines, note_lines = _extract_marked_lines(output)
    agent = ctx.db.get_agent(session.agent_id)
    author = agent.name if agent else None
    source_id = _parse_github_source_id(ticket_id)
    issue_number = _parse_github_issue_number(ticket_id)
    for line in public_lines:
        ctx.db.insert_comment(
            comment_id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            session_id=session.id,
            project_id=session.project_id,
            agent_id=session.agent_id,
            author=author,
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
            author=author,
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


def _store_output_comments(
    ctx: WorkItemContext, session: AgentSessionRecord, agent: Any, chunks: list[str]
) -> None:
    ticket_id = session.ticket_id
    if not ticket_id:
        return
    source_id = _parse_github_source_id(ticket_id)
    issue_number = _parse_github_issue_number(ticket_id)
    author = agent.name if agent else None
    for chunk in chunks:
        text = chunk.strip()
        if not text:
            continue
        ctx.db.insert_comment(
            comment_id=str(uuid.uuid4()),
            ticket_id=ticket_id,
            session_id=session.id,
            project_id=session.project_id,
            agent_id=session.agent_id,
            author=author,
            source_id=source_id,
            issue_number=issue_number,
            body=text,
            public=False,
            approved=False,
        )


async def _apply_agent_responses(
    ctx: WorkItemContext, session: AgentSessionRecord, spec: Any, output: str
) -> None:
    responses = ctx.db.list_agent_responses(agent_id=session.agent_id)
    if not responses:
        return
    match_text = _strip_control_sequences(output)
    for response in responses:
        raw_pattern = response.pattern.strip()
        if not raw_pattern:
            continue
        patterns = [line.strip() for line in raw_pattern.splitlines() if line.strip()]
        if not patterns:
            continue
        matched_all = True
        for pattern in patterns:
            try:
                if not re.search(pattern, match_text, flags=re.IGNORECASE | re.MULTILINE):
                    matched_all = False
                    break
            except re.error:
                matched_all = False
                break
        if matched_all:
            text = response.response.strip()
            if not text:
                continue
            agent = ctx.db.get_agent(session.agent_id)
            author = agent.name if agent else None
            ctx.db.insert_comment(
                comment_id=str(uuid.uuid4()),
                ticket_id=session.ticket_id,
                session_id=session.id,
                project_id=session.project_id,
                agent_id=session.agent_id,
                author=author,
                source_id=_parse_github_source_id(session.ticket_id),
                issue_number=_parse_github_issue_number(session.ticket_id),
                body=f"[auto-response] {text}",
                public=False,
                approved=False,
            )
            send_input(spec, session, text)




def _chunk_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks = []
    start = 0
    while start < len(text):
        chunks.append(text[start : start + size])
        start += size
    return chunks


def _extract_response_blocks(text: str, prefix: Optional[str]) -> list[str]:
    if not prefix:
        return [text]
    blocks: list[str] = []
    current: list[str] = []
    stripped_prefix = prefix.strip()
    for line in text.splitlines(keepends=True):
        raw = line
        plain = _strip_control_sequences(raw).lstrip()
        if raw.endswith("\n") and not plain.endswith("\n"):
            plain = f"{plain}\n"
        if plain.startswith(stripped_prefix):
            if current:
                blocks.append("".join(current).strip())
                current = []
            content = plain[len(stripped_prefix):].lstrip()
            current.append(content)
            continue
        if current:
            current.append(plain)
    if current:
        blocks.append("".join(current).strip())
    return [block for block in blocks if block]


def _strip_control_sequences(text: str) -> str:
    cleaned = re.sub(r"\r", "", text)
    cleaned = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", cleaned)
    cleaned = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", cleaned)
    cleaned = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", cleaned)
    cleaned = re.sub(r"\[[0-9;?<>]*[A-Za-z]", "", cleaned)
    cleaned = re.sub(r"\[[0-9;?<>]*[\\\"]", "", cleaned)
    cleaned = re.sub(r"\][0-9;?<>]*[\\\"]", "", cleaned)
    return cleaned


def _strip_echo_lines(text: str, prefix: Optional[str]) -> str:
    if not prefix:
        return text
    kept: list[str] = []
    for line in text.splitlines(keepends=True):
        plain = _strip_control_sequences(line)
        if prefix in plain:
            continue
        kept.append(line)
    return "".join(kept)


def _should_flush_buffer(updated_at: Optional[str], seconds: int) -> bool:
    if not updated_at:
        return False
    try:
        updated = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    now = datetime.now(timezone.utc)
    return (now - updated).total_seconds() >= seconds


async def _emit_output(
    ctx: WorkItemContext,
    session: AgentSessionRecord,
    project: Any,
    agent: Any,
    text: str,
) -> None:
    prefix = f"[{agent.slug}] "
    cleaned = _strip_echo_lines(text, agent.input_echo_prefix)
    blocks = _extract_response_blocks(cleaned, agent.response_prefix)
    comment_chunks: list[str] = []
    for block in blocks:
        comment_chunks.extend(_chunk_text(block, 3000))
    if project.slack_channel_id and session.thread_ts and ctx.tools.get("slack_post_message"):
        for chunk in _chunk_text(cleaned, 3000):
            await ctx.tools.call(
                "slack_post_message",
                {
                    "channel": project.slack_channel_id,
                    "thread_ts": session.thread_ts,
                    "text": prefix + chunk,
                },
            )
    _store_output_comments(ctx, session, agent, comment_chunks)


async def _maybe_send_prompt(
    ctx: WorkItemContext,
    session: AgentSessionRecord,
    spec: Any,
    buffer_text: str,
    quiet_seconds: int = 4,
) -> None:
    if not session.prompt_pending:
        return
    if buffer_text.strip():
        return
    if not _should_flush_buffer(session.last_output_at, seconds=quiet_seconds):
        return
    text = session.prompt_pending.strip()
    if not text:
        ctx.db.update_session(session.id, prompt_pending="")
        return
    send_input(spec, session, text)
    ctx.db.update_session(
        session.id,
        prompt_pending="",
        prompt_sent_at=utc_now(),
    )


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
