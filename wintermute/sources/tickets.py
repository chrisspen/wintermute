"""Ticket auto-start TaskSource."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from typing import Any, Optional
import uuid

from wintermute.db import Database, TicketRecord
from wintermute.prompts import render_prompt_template
from wintermute.runner import (
    build_ssh_spec,
    build_ssh_spec_with_options,
    ensure_repo,
    is_codex_command,
    parse_ssh_options,
    prepare_ticket_branch,
    set_codex_trust,
    start_session,
    strip_port_forwards,
)
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft, WorkItemBlocked
from wintermute.tickets import parse_issue_ticket


def _resolve_ticket_agent_id(ticket: TicketRecord) -> Optional[str]:
    if ticket.agent_id:
        return ticket.agent_id
    assigned = ticket.assigned_to or ""
    if assigned.startswith("agent:"):
        agent_id = assigned.split(":", 1)[1].strip()
        return agent_id or None
    return None


@dataclass
class TicketAutoStartWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]

    async def resume(self, ctx: WorkItemContext) -> None:
        ticket_id = self.checkpoint.get("ticket_id")
        if not ticket_id:
            return
        provider, _source_id, _issue_number = parse_issue_ticket(ticket_id)
        if provider:
            return
        ticket = ctx.db.get_ticket(ticket_id)
        if not ticket or not ticket.auto_start or ticket.status != "open":
            return
        await self._auto_start(ctx, ticket)

    async def _auto_start(self, ctx: WorkItemContext, ticket: TicketRecord) -> None:
        logger = logging.getLogger(__name__)
        project = ctx.db.get_project(ticket.project_id)
        if not project:
            return
        agent_id = _resolve_ticket_agent_id(ticket)
        if agent_id and agent_id != ticket.agent_id:
            ctx.db.update_ticket(ticket.id, agent_id=agent_id)
        if not agent_id:
            await self._notify(ctx, project.slack_channel_id, "Ticket missing agent assignment.")
            return
        agent = ctx.db.get_agent(agent_id)
        if not agent:
            await self._notify(ctx, project.slack_channel_id, "Ticket agent not found.")
            return
        project_vm = ctx.db.get_project_vm_for_project(project.id)
        if not project_vm:
            await self._notify(ctx, project.slack_channel_id, "No VM mapping configured for this project.")
            return
        vm = ctx.db.get_vm_target(project_vm.vm_target_id)
        if not vm:
            await self._notify(ctx, project.slack_channel_id, "VM target not found for this project.")
            return
        if ctx.db.get_session_by_ticket(ticket.id):
            logger.info("Session already exists for ticket %s", ticket.id)
            return
        if project_vm.repo_mode == "mirror":
            running = ctx.db.list_sessions(project_id=project.id, status="running")
            for session in running:
                if session.project_vm_id == project_vm.id:
                    logger.info("Project session already running for %s", project.id)
                    raise WorkItemBlocked("Project session already running", delay_seconds=60)
        session_spec = build_ssh_spec(vm, agent.required_ssh_options)
        base_options = strip_port_forwards(parse_ssh_options(agent.required_ssh_options))
        base_spec = build_ssh_spec_with_options(vm, base_options)
        title_line = f"Ticket: {ticket.title}".strip()
        thread_ts = await self._notify(
            ctx,
            project.slack_channel_id,
            f"{title_line}\n{ticket.source_url or ''}\nStarting agent session...",
        )
        safe_id = re.sub(r"[^a-zA-Z0-9]+", "-", ticket.id.strip().lower()).strip("-")
        short_id = safe_id[:10] if safe_id else "ticket"
        session_id = f"{project.slug}-{agent.slug}-ticket-{short_id}"
        repo_resource, resource_error = ctx.db.acquire_repo_resource(
            project=project,
            project_vm=project_vm,
            session_id=session_id,
            agent_id=agent.id,
        )
        if not repo_resource:
            message = resource_error or "Repo resource unavailable"
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=300)
        try:
            repo_path = ensure_repo(base_spec, project_vm, repo_path=repo_resource.path)
        except Exception as exc:
            ctx.db.release_repo_resource_for_session(session_id)
            message = f"Repo setup failed: {exc}"
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=60) from exc
        if not repo_path:
            ctx.db.release_repo_resource_for_session(session_id)
            message = "Repository not configured for this project VM."
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=300)
        if is_codex_command(agent.command) and agent.trust_level:
            set_codex_trust(base_spec, repo_path, agent.trust_level)
        try:
            branch_name = prepare_ticket_branch(base_spec, repo_path, ticket.id)
        except Exception as exc:
            ctx.db.release_repo_resource_for_session(session_id)
            message = f"Branch prep failed: {exc}"
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=60) from exc
        ctx.db.insert_session(
            session_id=session_id,
            project_id=project.id,
            project_vm_id=project_vm.id,
            agent_id=agent.id,
            ticket_id=ticket.id,
            status="running",
            repo_path=repo_path,
            thread_ts=thread_ts,
        )
        logger.info("Started session %s for ticket %s", session_id, ticket.id)
        if agent.session_mode != "mcp":
            start_session(session_spec, session_id, agent, repo_path)
        prompt = _ticket_prompt(
            title=ticket.title,
            description=ticket.description or "",
            source_url=ticket.source_url,
            internal_notes=ticket.internal_notes,
            repo_path=repo_path,
            branch_name=branch_name,
            project_name=project.name,
            project_slug=project.slug,
            prompt_template=project.prompt_template,
        )
        session = ctx.db.get_session(session_id)
        if session:
            ctx.db.update_session(session_id, prompt_pending=prompt)
            ctx.db.insert_comment(
                comment_id=str(uuid.uuid4()),
                ticket_id=ticket.id,
                session_id=session_id,
                project_id=project.id,
                agent_id=agent.id,
                author="auto",
                source_id=None,
                issue_number=None,
                body=prompt,
                public=False,
                approved=True,
            )
        if ticket.status == "open":
            ctx.db.update_ticket(ticket.id, status="in-progress")
        if thread_ts:
            await self._notify(
                ctx,
                project.slack_channel_id,
                f"[{agent.slug}] session started in {repo_path}",
                thread_ts=thread_ts,
            )

    async def _notify(
        self, ctx: WorkItemContext, channel: Optional[str], text: str, thread_ts: Optional[str] = None
    ) -> Optional[str]:
        if not channel:
            return None
        if not ctx.tools.get("slack_post_message"):
            return None
        response = await ctx.tools.call(
            "slack_post_message",
            {
                "channel": channel,
                "thread_ts": thread_ts,
                "text": text,
            },
        )
        return response.get("ts")


def _ticket_prompt(
    *,
    title: str,
    description: str,
    source_url: Optional[str],
    internal_notes: Optional[str],
    repo_path: str,
    branch_name: str,
    project_name: str,
    project_slug: str,
    prompt_template: Optional[str],
) -> str:
    lines = [
        "You are a coding agent working on the following ticket.",
        "",
        f"Title: {title}",
    ]
    if description:
        lines.extend(["", "Description:", description])
    if source_url:
        lines.extend(["", f"Source URL: {source_url}"])
    if internal_notes:
        lines.extend(["", "Internal notes:", internal_notes])
    lines.extend(
        [
            "",
            f"Repo path: {repo_path}",
            f"Branch: {branch_name}",
            "",
            "Please do the work, commit your changes, and push the branch for review.",
            "Work autonomously until you hit a blocker.",
            "If you need help or clarification, ask your questions clearly and include a line starting with 'BLOCKER:' summarizing what you need.",
            "Then stop this session so the supervisor can move you to other work while you wait.",
        ]
    )
    default_prompt = "\n".join(lines)
    context = {
        "project_name": project_name,
        "project_slug": project_slug,
        "repo_path": repo_path,
        "branch_name": branch_name,
        "title": title,
        "description": description,
        "url": source_url or "",
        "internal_notes": internal_notes or "",
        "issue_number": "",
        "owner": "",
        "repo": "",
        "comments": "",
    }
    return render_prompt_template(prompt_template, default_prompt, context)


class TicketAutoStartSource(TaskSource):
    id = "ticket_auto_start"
    enabled = False
    base_priority = 65
    poll_interval_seconds = 30

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        source = db.get_task_source(self.id)
        if not source or not source.enabled:
            return []
        drafts: list[WorkItemDraft] = []
        for ticket in db.list_auto_start_tickets():
            provider, _source_id, _issue_number = parse_issue_ticket(ticket.id)
            if provider:
                continue
            work_id = f"ticket:{ticket.id}:{ticket.updated_at}"
            drafts.append(
                WorkItemDraft(
                    work_id=work_id,
                    priority=source.base_priority,
                    source_id=self.id,
                    checkpoint={"ticket_id": ticket.id},
                )
            )
        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        return TicketAutoStartWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            checkpoint=record.checkpoint,
        )
