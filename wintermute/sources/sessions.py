"""Agent session output polling source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
import logging
import re
import json
import os

import uuid

from wintermute.db import Database, AgentSessionRecord, utc_now
from wintermute.claude_client import close_claude_process, poll_claude, run_claude_prompt
from wintermute.mcp_client import close_mcp_process, poll_codex_mcp, run_codex_mcp
from wintermute.runner import (
    build_ssh_spec,
    build_ssh_spec_with_options,
    is_session_running,
    parse_ssh_options,
    read_output,
    send_input,
    strip_port_forwards,
)
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft
from wintermute.tickets import parse_issue_ticket


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
        project = ctx.db.get_project(session.project_id)
        project_vm = ctx.db.get_project_vm(session.project_vm_id)
        agent = ctx.db.get_agent(session.agent_id)
        vm = ctx.db.get_vm_target(project_vm.vm_target_id) if project_vm else None
        if not (project and project_vm and agent and vm):
            return
        if agent.session_mode == "mcp":
            await _run_mcp_session(ctx, session, project, agent, project_vm, vm)
            return
        if agent.session_mode == "claude":
            await _run_claude_session(ctx, session, project, agent, project_vm, vm)
            return
        if session.status != "running":
            ctx.db.release_repo_resource_for_session(session.id)
            if session.output_buffer:
                cleaned_buffer = _strip_echo_lines(session.output_buffer, agent.input_echo_prefix)
                await _emit_output(ctx, session, project, agent, cleaned_buffer, force_comment=True)
                ctx.db.update_session(
                    session.id,
                    output_buffer="",
                    output_buffer_updated_at=utc_now(),
                )
            return
        spec = build_ssh_spec(vm, agent.required_ssh_options)
        if not is_session_running(spec, session.id):
            if session.output_buffer:
                cleaned_buffer = _strip_echo_lines(session.output_buffer, agent.input_echo_prefix)
                await _emit_output(ctx, session, project, agent, cleaned_buffer, force_comment=True)
                ctx.db.update_session(
                    session.id,
                    output_buffer="",
                    output_buffer_updated_at=utc_now(),
                )
            ctx.db.update_session(session.id, status="done")
            ctx.db.release_repo_resource_for_session(session.id)
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
            combined = buffer_text + output
            ctx.db.update_session(
                session.id,
                last_output=output,
                last_output_offset=new_offset,
                output_buffer=combined,
                output_buffer_updated_at=utc_now(),
                last_output_at=utc_now(),
            )
            if session.awaiting_response and len(combined) >= 4000:
                response_window = _slice_response_window(session, combined)
                cleaned_buffer = _strip_echo_lines(response_window, agent.input_echo_prefix)
                stored = await _emit_output(
                    ctx,
                    session,
                    project,
                    agent,
                    cleaned_buffer,
                    force_comment=True,
                )
                await _apply_agent_responses(
                    ctx,
                    session,
                    cleaned_buffer,
                    sender=lambda text: send_input(spec, session, text),
                )
                await _handle_session_markers(ctx, session, cleaned_buffer)
                if stored:
                    ctx.db.update_session(
                        session.id,
                        awaiting_response=0,
                        last_user_message="",
                        awaiting_response_offset=0,
                    )
                ctx.db.update_session(
                    session.id,
                    output_buffer="",
                    output_buffer_updated_at=utc_now(),
                )
            else:
                await _maybe_send_prompt(ctx, session, spec, combined)
            return
        if buffer_text and _should_flush_buffer(buffer_updated_at, seconds=4):
            response_window = _slice_response_window(session, buffer_text)
            cleaned_buffer = _strip_echo_lines(response_window, agent.input_echo_prefix)
            stored = await _emit_output(
                ctx,
                session,
                project,
                agent,
                cleaned_buffer,
                force_comment=bool(session.awaiting_response),
            )
            await _apply_agent_responses(
                ctx,
                session,
                cleaned_buffer,
                sender=lambda text: send_input(spec, session, text),
            )
            await _handle_session_markers(ctx, session, cleaned_buffer)
            if session.awaiting_response and stored:
                ctx.db.update_session(
                    session.id,
                    awaiting_response=0,
                    last_user_message="",
                    awaiting_response_offset=0,
                )
            ctx.db.update_session(
                session.id,
                output_buffer="",
                output_buffer_updated_at=utc_now(),
            )
        await _maybe_send_prompt(ctx, session, spec, buffer_text)
        await _maybe_send_queued_input(ctx, session, agent, spec)
        return


async def _run_mcp_session(
    ctx: WorkItemContext,
    session: AgentSessionRecord,
    project: Any,
    agent: Any,
    project_vm: Any,
    vm: Any,
) -> None:
    logger = logging.getLogger(__name__)
    keepalive_seconds = int(os.environ.get("WINTERMUTE_MCP_KEEPALIVE_SECONDS", "600"))
    if session.status != "running":
        close_mcp_process(session.id)
        ctx.db.release_repo_resource_for_session(session.id)
        return
    base_options = strip_port_forwards(parse_ssh_options(agent.required_ssh_options))
    spec = build_ssh_spec_with_options(vm, base_options)
    conversation_id = session.mcp_conversation_id

    def _send_prompt(prompt: str) -> Any:
        nonlocal conversation_id
        result = run_codex_mcp(
            spec,
            agent,
            session_id=session.id,
            prompt=prompt,
            cwd=session.repo_path,
            conversation_id=conversation_id,
        )
        if result.conversation_id and result.conversation_id != conversation_id:
            conversation_id = result.conversation_id
            ctx.db.update_session(session.id, mcp_conversation_id=conversation_id)
        return result

    def _handle_mcp_error(error: Optional[str]) -> bool:
        if not error:
            return False
        lowered = error.lower()
        if "mcp process" in lowered:
            logger.warning("MCP process error for session %s: %s", session.id, error)
            close_mcp_process(session.id)
            ctx.db.update_session(session.id, status="done", awaiting_response=0)
            ctx.db.release_repo_resource_for_session(session.id)
            return True
        return False

    if session.prompt_pending:
        prompt = session.prompt_pending.strip()
        ctx.db.update_session(session.id, prompt_pending="", prompt_sent_at=utc_now())
        if prompt:
            logger.info("MCP prompt queued for session %s", session.id)
            result = _send_prompt(prompt)
            if _handle_mcp_error(result.error):
                return
            if result.error:
                logger.warning("MCP prompt error for session %s: %s", session.id, result.error)
            if result.response_text:
                logger.info("MCP prompt response received for session %s", session.id)
                await _emit_output(ctx, session, project, agent, result.response_text, force_comment=True)
                await _handle_session_markers(ctx, session, result.response_text)
                await _apply_agent_responses(
                    ctx,
                    session,
                    result.response_text,
                    sender=lambda text: _send_prompt(text),
                )
                ctx.db.update_session(session.id, awaiting_response=0, last_output_at=utc_now())
            else:
                logger.info("MCP prompt returned no response for session %s", session.id)
                ctx.db.update_session(session.id, awaiting_response=1)

    raw_queue = session.queued_user_messages or "[]"
    try:
        queue = json.loads(raw_queue)
        if not isinstance(queue, list):
            queue = []
    except json.JSONDecodeError:
        queue = []
    if not queue:
        if session.awaiting_response:
            poll_result = poll_codex_mcp(session.id, timeout_seconds=5)
            if _handle_mcp_error(poll_result.error):
                return
            if poll_result.error:
                logger.warning("MCP poll error for session %s: %s", session.id, poll_result.error)
            if poll_result.response_text:
                logger.info("MCP poll response received for session %s", session.id)
                await _emit_output(ctx, session, project, agent, poll_result.response_text, force_comment=True)
                await _handle_session_markers(ctx, session, poll_result.response_text)
                await _apply_agent_responses(
                    ctx,
                    session,
                    poll_result.response_text,
                    sender=lambda text: _send_prompt(text),
                )
                ctx.db.update_session(session.id, awaiting_response=0, last_output_at=utc_now())
        else:
            last_activity = session.last_output_at or session.prompt_sent_at or session.updated_at
            if last_activity and _should_flush_buffer(last_activity, seconds=keepalive_seconds):
                logger.info("MCP keepalive for session %s", session.id)
                keepalive_text = (
                    "[keepalive] Reply with a single '.' and nothing else."
                )
                keepalive_result = _send_prompt(keepalive_text)
                if keepalive_result.error or not keepalive_result.response_text:
                    logger.warning("MCP keepalive failed for session %s", session.id)
                    close_mcp_process(session.id)
                    ctx.db.update_session(session.id, status="done", awaiting_response=0)
                    ctx.db.release_repo_resource_for_session(session.id)
                else:
                    ctx.db.update_session(session.id, last_output_at=utc_now())
        return
    message = str(queue.pop(0))
    ctx.db.update_session(session.id, queued_user_messages=json.dumps(queue))
    if not message.strip():
        return
    logger.info("MCP reply queued for session %s", session.id)
    result = _send_prompt(message)
    if _handle_mcp_error(result.error):
        return
    if result.error:
        logger.warning("MCP reply error for session %s: %s", session.id, result.error)
    if result.response_text:
        logger.info("MCP reply response received for session %s", session.id)
        await _emit_output(ctx, session, project, agent, result.response_text, force_comment=True)
        await _handle_session_markers(ctx, session, result.response_text)
        await _apply_agent_responses(
            ctx,
            session,
            result.response_text,
            sender=lambda text: _send_prompt(text),
        )
        ctx.db.update_session(session.id, awaiting_response=0, last_output_at=utc_now())
    else:
        logger.info("MCP reply returned no response for session %s", session.id)
        ctx.db.update_session(session.id, awaiting_response=1)


async def _run_claude_session(
    ctx: WorkItemContext,
    session: AgentSessionRecord,
    project: Any,
    agent: Any,
    project_vm: Any,
    vm: Any,
) -> None:
    """Run a Claude Code CLI session.

    Uses a persistent subprocess with streaming JSON I/O, similar to MCP.
    The Claude process stays alive and prompts are sent via stdin.
    """
    logger = logging.getLogger(__name__)
    keepalive_seconds = int(os.environ.get("WINTERMUTE_CLAUDE_KEEPALIVE_SECONDS", "600"))

    if session.status != "running":
        close_claude_process(session.id)
        ctx.db.release_repo_resource_for_session(session.id)
        return

    base_options = strip_port_forwards(parse_ssh_options(agent.required_ssh_options))
    spec = build_ssh_spec_with_options(vm, base_options)

    def _send_prompt(prompt: str) -> Any:
        result = run_claude_prompt(
            spec,
            agent,
            session_id=session.id,
            prompt=prompt,
            cwd=session.repo_path,
            timeout_seconds=int(os.environ.get("WINTERMUTE_CLAUDE_TIMEOUT_SECONDS", "300")),
        )
        if result.session_id:
            ctx.db.update_session(session.id, claude_session_id=result.session_id)
        return result

    def _handle_claude_error(error: Optional[str]) -> bool:
        if not error:
            return False
        lowered = error.lower()
        # Check for fatal errors that should end the session
        if "exited" in lowered or "closed" in lowered:
            logger.warning("Claude process error for session %s: %s", session.id, error)
            close_claude_process(session.id)
            ctx.db.update_session(session.id, status="done", awaiting_response=0)
            ctx.db.release_repo_resource_for_session(session.id)
            return True
        return False

    # Handle pending prompt
    if session.prompt_pending:
        prompt = session.prompt_pending.strip()
        ctx.db.update_session(session.id, prompt_pending="", prompt_sent_at=utc_now())
        if prompt:
            logger.info("Claude prompt queued for session %s", session.id)
            result = _send_prompt(prompt)
            if _handle_claude_error(result.error):
                return
            if result.error:
                logger.warning("Claude prompt error for session %s: %s", session.id, result.error)
            if result.response_text:
                logger.info("Claude prompt response received for session %s", session.id)
                await _emit_output(ctx, session, project, agent, result.response_text, force_comment=True)
                await _handle_session_markers(ctx, session, result.response_text)
                await _apply_agent_responses(
                    ctx,
                    session,
                    result.response_text,
                    sender=lambda text: _send_prompt(text),
                )
                ctx.db.update_session(session.id, awaiting_response=0, last_output_at=utc_now())
            else:
                logger.info("Claude prompt returned no response for session %s", session.id)
                ctx.db.update_session(session.id, awaiting_response=1)

    # Handle queued user messages
    raw_queue = session.queued_user_messages or "[]"
    try:
        queue = json.loads(raw_queue)
        if not isinstance(queue, list):
            queue = []
    except json.JSONDecodeError:
        queue = []

    if not queue:
        # Poll for any pending output (Claude may still be working)
        if session.awaiting_response:
            poll_result = poll_claude(session.id, timeout_seconds=5)
            if _handle_claude_error(poll_result.error):
                return
            if poll_result.error:
                logger.warning("Claude poll error for session %s: %s", session.id, poll_result.error)
            if poll_result.response_text:
                logger.info("Claude poll response received for session %s", session.id)
                await _emit_output(ctx, session, project, agent, poll_result.response_text, force_comment=True)
                await _handle_session_markers(ctx, session, poll_result.response_text)
                await _apply_agent_responses(
                    ctx,
                    session,
                    poll_result.response_text,
                    sender=lambda text: _send_prompt(text),
                )
                ctx.db.update_session(session.id, awaiting_response=0, last_output_at=utc_now())
        else:
            # Check for keepalive
            last_activity = session.last_output_at or session.prompt_sent_at or session.updated_at
            if last_activity and _should_flush_buffer(last_activity, seconds=keepalive_seconds):
                logger.info("Claude keepalive for session %s", session.id)
                keepalive_text = "[keepalive] Reply with a single '.' and nothing else."
                keepalive_result = _send_prompt(keepalive_text)
                if keepalive_result.error or not keepalive_result.response_text:
                    logger.warning("Claude keepalive failed for session %s", session.id)
                    close_claude_process(session.id)
                    ctx.db.update_session(session.id, status="done", awaiting_response=0)
                    ctx.db.release_repo_resource_for_session(session.id)
                else:
                    ctx.db.update_session(session.id, last_output_at=utc_now())
        return

    message = str(queue.pop(0))
    ctx.db.update_session(session.id, queued_user_messages=json.dumps(queue))
    if not message.strip():
        return

    logger.info("Claude reply queued for session %s", session.id)
    result = _send_prompt(message)
    if _handle_claude_error(result.error):
        return
    if result.error:
        logger.warning("Claude reply error for session %s: %s", session.id, result.error)
    if result.response_text:
        logger.info("Claude reply response received for session %s", session.id)
        await _emit_output(ctx, session, project, agent, result.response_text, force_comment=True)
        await _handle_session_markers(ctx, session, result.response_text)
        await _apply_agent_responses(
            ctx,
            session,
            result.response_text,
            sender=lambda text: _send_prompt(text),
        )
        ctx.db.update_session(session.id, awaiting_response=0, last_output_at=utc_now())
    else:
        logger.info("Claude reply returned no response for session %s", session.id)
        ctx.db.update_session(session.id, awaiting_response=1)


async def _handle_session_markers(
    ctx: WorkItemContext, session: AgentSessionRecord, output: str
) -> None:
    public_lines, note_lines, blocker_lines, standup_lines = _extract_marked_lines(output)
    if not (public_lines or note_lines or blocker_lines or standup_lines):
        return
    ticket_id = session.ticket_id
    agent = ctx.db.get_agent(session.agent_id)
    author = agent.name if agent else None
    agent_label = agent.slug if agent else "agent"
    _provider, source_id, issue_number = parse_issue_ticket(ticket_id) if ticket_id else (None, None, None)
    if ticket_id:
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
        for line in blocker_lines:
            ctx.db.insert_comment(
                comment_id=str(uuid.uuid4()),
                ticket_id=ticket_id,
                session_id=session.id,
                project_id=session.project_id,
                agent_id=session.agent_id,
                author=author,
                source_id=source_id,
                issue_number=issue_number,
                body=f"Blocker: {line}",
                public=False,
                approved=False,
            )
        for line in standup_lines:
            ctx.db.insert_comment(
                comment_id=str(uuid.uuid4()),
                ticket_id=ticket_id,
                session_id=session.id,
                project_id=session.project_id,
                agent_id=session.agent_id,
                author=author,
                source_id=source_id,
                issue_number=issue_number,
                body=f"Standup: {line}",
                public=False,
                approved=False,
            )
    if blocker_lines:
        if ticket_id:
            ticket = ctx.db.get_ticket(ticket_id)
            if ticket and ticket.status not in {"done", "needs-feedback"}:
                ctx.db.update_ticket(ticket_id, status="needs-feedback")
        ctx.db.update_session(session.id, status="blocked", awaiting_response=0)
        project = ctx.db.get_project(session.project_id)
        if project and project.slack_channel_id and session.thread_ts and ctx.tools.get("slack_post_message"):
            summary = "\n".join(f"- {line}" for line in blocker_lines)
            try:
                await ctx.tools.call(
                    "slack_post_message",
                    {
                        "channel": project.slack_channel_id,
                        "thread_ts": session.thread_ts,
                        "text": f"[{agent_label}] BLOCKER:\n{summary}",
                    },
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("Slack blocker notify failed: %s", exc)
    if standup_lines:
        standup_source = ctx.db.get_task_source("standup")
        standup_channel = None
        if standup_source:
            standup_channel = str(standup_source.config.get("channel") or "").strip() or None
        if standup_channel and ctx.tools.get("slack_post_message"):
            ticket = ctx.db.get_ticket(ticket_id) if ticket_id else None
            ticket_label = f"{ticket.title} ({ticket.id})" if ticket else ""
            summary = "\n".join(f"- {line}" for line in standup_lines)
            text = f"[{agent_label}] {ticket_label}\n{summary}".strip()
            try:
                await ctx.tools.call(
                    "slack_post_message",
                    {
                        "channel": standup_channel,
                        "text": text,
                    },
                )
            except Exception as exc:
                logging.getLogger(__name__).warning("Slack standup notify failed: %s", exc)


def _extract_marked_lines(output: str) -> tuple[list[str], list[str], list[str], list[str]]:
    public_lines: list[str] = []
    note_lines: list[str] = []
    blocker_lines: list[str] = []
    standup_lines: list[str] = []
    markers = [
        ("PUBLIC:", public_lines),
        ("GITHUB:", public_lines),
        ("GITLAB:", public_lines),
        ("NOTE:", note_lines),
        ("BLOCKER:", blocker_lines),
        ("STANDUP:", standup_lines),
    ]
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        upper = line.upper()
        for marker, bucket in markers:
            idx = upper.find(marker)
            if idx == -1:
                continue
            bucket.append(line[idx + len(marker):].strip())
            break
    return public_lines, note_lines, blocker_lines, standup_lines


def _store_output_comments(
    ctx: WorkItemContext, session: AgentSessionRecord, agent: Any, chunks: list[str]
) -> None:
    ticket_id = session.ticket_id
    if not ticket_id:
        return
    _provider, source_id, issue_number = parse_issue_ticket(ticket_id)
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
    ctx: WorkItemContext,
    session: AgentSessionRecord,
    output: str,
    sender: Optional[callable] = None,
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
            if sender:
                sender(text)




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
        match = plain.find(stripped_prefix) if stripped_prefix else -1
        if match != -1:
            if current:
                blocks.append("".join(current).strip())
                current = []
            content = plain[match + len(stripped_prefix):].lstrip()
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
    cleaned = re.sub(r"\[[0-9][0-9;?<>]*[A-Za-z]", "", cleaned)
    cleaned = re.sub(r"\[[0-9][0-9;?<>]*[\\\"]", "", cleaned)
    cleaned = re.sub(r"\][0-9][0-9;?<>]*[\\\"]", "", cleaned)
    cleaned = re.sub(r"\[(?:;)*[A-Za-z]", "", cleaned)
    cleaned = re.sub(r"\[(?:;)*[\\\"]", "", cleaned)
    cleaned = re.sub(r"\](?:;)*[\\\"]", "", cleaned)
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


def _slice_response_window(session: AgentSessionRecord, text: str) -> str:
    if not session.awaiting_response_offset:
        return text
    encoded = text.encode("utf-8", errors="ignore")
    buffer_start = session.last_output_offset - len(encoded)
    if buffer_start < 0:
        buffer_start = 0
    if session.awaiting_response_offset <= buffer_start:
        return text
    skip = session.awaiting_response_offset - buffer_start
    if skip >= len(encoded):
        return ""
    return encoded[skip:].decode("utf-8", errors="ignore")


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
    *,
    force_comment: bool = False,
) -> bool:
    prefix = f"[{agent.slug}] "
    cleaned = _strip_echo_lines(text, agent.input_echo_prefix)
    blocks = _extract_response_blocks(cleaned, agent.response_prefix)
    comment_chunks: list[str] = []
    for block in blocks:
        comment_chunks.extend(_chunk_text(block, 3000))
    if force_comment and not comment_chunks:
        sanitized = _strip_control_sequences(cleaned)
        if session.last_user_message:
            sanitized = sanitized.replace(session.last_user_message, "").strip()
        if sanitized and len(sanitized) >= 5 and re.search(r"[A-Za-z]", sanitized):
            comment_chunks = _chunk_text(sanitized, 3000)
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
    return bool(comment_chunks)


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


async def _maybe_send_queued_input(
    ctx: WorkItemContext,
    session: AgentSessionRecord,
    agent: Any,
    spec: Any,
) -> None:
    if session.awaiting_response:
        return
    if session.prompt_pending:
        return
    raw_queue = session.queued_user_messages or "[]"
    try:
        queue = json.loads(raw_queue)
        if not isinstance(queue, list):
            queue = []
    except json.JSONDecodeError:
        queue = []
    if not queue:
        return
    message = str(queue.pop(0))
    if agent and agent.response_prefix and agent.response_prefix not in message:
        message = f"{message}\n\nPlease reply with lines starting with '{agent.response_prefix}'."
    send_input(spec, session, message)
    ctx.db.update_session(
        session.id,
        queued_user_messages=json.dumps(queue),
        awaiting_response=1,
        last_user_message=message,
        awaiting_response_offset=session.last_output_offset,
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
