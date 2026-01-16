"""GitLab issues TaskSource."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import logging
import os
from typing import Any, Optional
import urllib.parse

import aiohttp

from wintermute.db import Database
from wintermute.prompts import render_prompt_template
from wintermute.runner import (
    build_ssh_spec,
    build_ssh_spec_with_options,
    configure_git_push_auth,
    ensure_repo,
    is_codex_command,
    parse_ssh_options,
    prepare_issue_branch,
    set_codex_trust,
    start_session,
    strip_port_forwards,
)
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft, WorkItemBlocked


GITLAB_API_BASE = os.environ.get("WINTERMUTE_GITLAB_API_BASE", "https://gitlab.com/api/v4").rstrip("/")


def _encode_project_id(project_id: str) -> str:
    return urllib.parse.quote(project_id, safe="")


async def _fetch_issue_comments(
    token: str,
    project_id: str,
    issue_iid: int,
    base_url: Optional[str] = None,
) -> list[dict[str, Any]]:
    headers = {
        "Accept": "application/json",
        "PRIVATE-TOKEN": token,
        "User-Agent": "wintermute",
    }
    encoded = _encode_project_id(project_id)
    if base_url:
        api_base = base_url.rstrip("/")
        if not api_base.endswith("/api/v4"):
            api_base = f"{api_base}/api/v4"
    else:
        api_base = GITLAB_API_BASE
    url = f"{api_base}/projects/{encoded}/issues/{issue_iid}/notes"
    comments: list[dict[str, Any]] = []
    page = 1
    async with aiohttp.ClientSession() as session:
        while True:
            async with session.get(
                url, headers=headers, params={"per_page": 100, "page": page}
            ) as response:
                payload = await response.json()
                if response.status >= 400:
                    return []
                if not isinstance(payload, list):
                    return comments
                comments.extend(payload)
                if len(payload) < 100:
                    return comments
                page += 1


def _tool_schema(ctx: WorkItemContext) -> list[dict[str, Any]]:
    return [asdict(definition) for definition in ctx.tools.definitions()]


def _split_project_path(project_path: str) -> tuple[str, str]:
    parts = [part for part in project_path.split("/") if part]
    if not parts:
        return project_path, project_path
    if len(parts) == 1:
        return parts[0], parts[0]
    return "/".join(parts[:-1]), parts[-1]


@dataclass
class GitLabIssueWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
        source_id = self.checkpoint.get("gitlab_source_id")
        source = ctx.db.get_gitlab_source(source_id) if source_id else None
        if source:
            self._sync_ticket(ctx, source)
        if source and source.auto_start:
            logger.info("GitLab work item %s auto-start enabled", self.work_id)
            await self._auto_start(ctx, source)
            return
        logger.info("GitLab work item %s using LLM decision path", self.work_id)
        agent = ctx.db.get_agent(source.agent_id) if source and source.agent_id else None
        await self._llm_decide(ctx, agent)

    async def _llm_decide(self, ctx: WorkItemContext, agent: Any) -> None:
        decision = ctx.executor.decide_next_action(
            state=dict(self.checkpoint),
            observation={"issue": dict(self.checkpoint)},
            tool_schema=_tool_schema(ctx),
            base_url=agent.llm_base_url if agent else None,
            api_key=agent.llm_api_key if agent else None,
            model=agent.llm_model if agent else None,
        )
        if decision.type == "tool":
            result = await ctx.tools.call(decision.payload["name"], decision.payload["args"])
            await ctx.checkpoint(
                {
                    "last_tool": decision.payload["name"],
                    "last_tool_args": decision.payload["args"],
                    "last_tool_result": result,
                }
            )
            return
        if decision.type == "update":
            await ctx.checkpoint(decision.payload["patch"])
            return
        if decision.type == "done":
            await ctx.checkpoint({"summary": decision.payload["summary"]})
            return
        if decision.type == "escalate":
            await ctx.checkpoint(
                {
                    "escalate": {
                        "priority": decision.payload["priority"],
                        "reason": decision.payload["reason"],
                    }
                }
            )
            return
        if decision.type == "yield":
            await ctx.checkpoint({"yield_reason": decision.payload["reason"]})
            return

    def _sync_ticket(self, ctx: WorkItemContext, source: Any) -> None:
        issue_number = self.checkpoint.get("issue_number")
        if issue_number is None:
            return
        ticket_id = f"gitlab:{source.id}:{issue_number}"
        issue_title = str(self.checkpoint.get("title") or "")
        issue_body = str(self.checkpoint.get("body") or "")
        issue_url = self.checkpoint.get("web_url") or ""
        existing_ticket = ctx.db.get_ticket(ticket_id)
        if not existing_ticket:
            assigned_to = None
            if source.agent_id:
                agent = ctx.db.get_agent(source.agent_id)
                assigned_to = agent.name if agent else None
            ctx.db.insert_ticket(
                ticket_id=ticket_id,
                project_id=source.project_id,
                agent_id=source.agent_id or None,
                title=issue_title or f"GitLab issue #{issue_number}",
                description=issue_body,
                assigned_to=assigned_to,
                estimate=None,
                status="open",
                internal_notes=None,
                source_url=issue_url or None,
                auto_start=source.auto_start,
            )
            return
        ctx.db.update_ticket(
            ticket_id,
            title=issue_title or existing_ticket.title,
            description=issue_body or existing_ticket.description,
            source_url=issue_url or existing_ticket.source_url,
        )
        if not existing_ticket.agent_id and source.agent_id:
            ctx.db.update_ticket(ticket_id, agent_id=source.agent_id)

    async def _auto_start(self, ctx: WorkItemContext, source: Any) -> None:
        logger = logging.getLogger(__name__)
        project = ctx.db.get_project(source.project_id)
        if not project:
            return
        if not source.agent_id:
            await self._notify(ctx, project.slack_channel_id, "GitLab source missing agent assignment.")
            return
        agent = ctx.db.get_agent(source.agent_id)
        if not agent:
            await self._notify(ctx, project.slack_channel_id, "GitLab source agent not found.")
            return
        if not agent.vm_target_id:
            await self._notify(ctx, project.slack_channel_id, "Agent has no VM target configured.")
            return
        vm = ctx.db.get_vm_target(agent.vm_target_id)
        if not vm:
            await self._notify(ctx, project.slack_channel_id, "VM target not found for this agent.")
            return
        issue_number = self.checkpoint.get("issue_number")
        if issue_number is None:
            return
        issue_url = self.checkpoint.get("web_url") or ""
        ticket_id = f"gitlab:{source.id}:{issue_number}"
        existing_ticket = ctx.db.get_ticket(ticket_id)
        issue_title = str(self.checkpoint.get("title") or "")
        issue_body = str(self.checkpoint.get("body") or "")
        if not existing_ticket:
            ctx.db.insert_ticket(
                ticket_id=ticket_id,
                project_id=project.id,
                agent_id=agent.id,
                title=issue_title or f"GitLab issue #{issue_number}",
                description=issue_body,
                assigned_to=agent.name,
                estimate=None,
                status="open",
                internal_notes=None,
                source_url=issue_url or None,
            )
        else:
            ctx.db.update_ticket(
                ticket_id,
                title=issue_title or existing_ticket.title,
                description=issue_body or existing_ticket.description,
                source_url=issue_url or existing_ticket.source_url,
            )
            if not existing_ticket.agent_id:
                ctx.db.update_ticket(ticket_id, agent_id=agent.id)
        if ctx.db.get_session_by_ticket(ticket_id):
            logger.info("Session already exists for ticket %s", ticket_id)
            return
        if project.repo_mode == "mirror":
            running = ctx.db.list_sessions(project_id=project.id, status="running")
            if running:
                logger.info("Project session already running for %s", project.id)
                raise WorkItemBlocked("Project session already running", delay_seconds=60)
        session_spec = build_ssh_spec(vm, agent.required_ssh_options)
        base_options = strip_port_forwards(parse_ssh_options(agent.required_ssh_options))
        base_spec = build_ssh_spec_with_options(vm, base_options)
        token_record = ctx.db.get_gitlab_token(source.token_id) if source.token_id else None
        comments: list[dict[str, Any]] = []
        if token_record:
            try:
                comments = await _fetch_issue_comments(
                    token_record.token,
                    source.project_path,
                    int(issue_number),
                    base_url=token_record.base_url,
                )
            except Exception as exc:
                logger.warning("Failed to fetch issue comments: %s", exc)
        title_line = f"Issue #{issue_number}: {issue_title}".strip()
        thread_ts = await self._notify(
            ctx,
            project.slack_channel_id,
            f"{title_line}\n{issue_url}\nStarting agent session...",
        )
        session_id = f"{project.slug}-{agent.slug}-issue-{issue_number}"
        repo_resource, resource_error = ctx.db.acquire_repo_resource(
            project=project,
            session_id=session_id,
            agent_id=agent.id,
        )
        if not repo_resource:
            message = resource_error or "Repo resource unavailable"
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=300)
        try:
            repo_path = ensure_repo(base_spec, project, repo_path=repo_resource.path)
        except Exception as exc:
            ctx.db.release_repo_resource_for_session(session_id)
            message = f"Repo setup failed: {exc}"
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=60) from exc
        if not repo_path:
            ctx.db.release_repo_resource_for_session(session_id)
            message = "Repository not configured for this project."
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=300)
        if token_record and project.repo_url:
            configure_git_push_auth(
                base_spec,
                repo_path,
                project.repo_url,
                token_record.token,
                username="oauth2",
            )
        if is_codex_command(agent.command) and agent.trust_level:
            set_codex_trust(base_spec, repo_path, agent.trust_level)
        try:
            branch_name = prepare_issue_branch(base_spec, repo_path, int(issue_number))
        except Exception as exc:
            ctx.db.release_repo_resource_for_session(session_id)
            message = f"Branch prep failed: {exc}"
            await self._notify(ctx, project.slack_channel_id, message)
            raise WorkItemBlocked(message, delay_seconds=60) from exc
        ctx.db.insert_session(
            session_id=session_id,
            project_id=project.id,
            agent_id=agent.id,
            ticket_id=ticket_id,
            status="running",
            repo_path=repo_path,
            thread_ts=thread_ts,
        )
        logger.info("Started session %s for issue %s", session_id, issue_number)
        if agent.session_mode != "mcp":
            start_session(session_spec, session_id, agent, repo_path)
        session = ctx.db.get_session(session_id)
        if session:
            internal_notes = None
            if existing_ticket:
                internal_notes = existing_ticket.internal_notes
            prompt = _issue_prompt(
                self.checkpoint,
                source.project_path,
                comments=comments,
                internal_notes=internal_notes,
                branch_name=branch_name,
                repo_path=repo_path,
                project_name=project.name,
                project_slug=project.slug,
                prompt_template=project.prompt_template,
            )
            ctx.db.update_session(session_id, prompt_pending=prompt)
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


def _issue_prompt(
    issue: dict[str, Any],
    project_path: str,
    *,
    comments: list[dict[str, Any]],
    internal_notes: Optional[str],
    branch_name: str,
    repo_path: str,
    project_name: str,
    project_slug: str,
    prompt_template: Optional[str],
) -> str:
    issue_number = issue.get("issue_number")
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    url = issue.get("web_url") or ""
    comments_text = ""
    if comments:
        formatted = []
        for comment in comments:
            author = (comment.get("author") or {}).get("username") or "unknown"
            created_at = comment.get("created_at") or ""
            text = comment.get("body") or ""
            formatted.append(f"- {author} ({created_at}):\n{text}")
        comments_text = "\n".join(formatted)
    notes_text = internal_notes or ""
    default_prompt = (
        "You are working on a GitLab issue.\n"
        f"Project: {project_path}\n"
        f"Issue #{issue_number}: {title}\n"
        f"URL: {url}\n\n"
        "Instructions:\n"
        "- A working branch has been created for you.\n"
        f"- Branch name: {branch_name}.\n"
        "- Implement the fix, commit changes with a clear message, and push the branch.\n"
        "- Work autonomously until you hit a blocker.\n"
        "- Post status updates and questions in the Slack thread for this issue.\n"
        "- If you need help or clarification, ask in Slack and include a line starting with 'BLOCKER:' summarizing what you need.\n"
        "- Then stop this session so the supervisor can move you to other work while you wait.\n"
        "- For GitLab comments, prefix lines with 'PUBLIC:'; they will be stored for approval.\n"
        "- For internal notes, prefix lines with 'NOTE:' so they stay private.\n\n"
        "Issue description:\n"
        f"{body}\n\n"
        "Issue comments:\n"
        f"{comments_text or 'No comments yet.'}\n\n"
        "Internal notes:\n"
        f"{notes_text or 'No internal notes yet.'}\n"
    )
    owner, repo = _split_project_path(project_path)
    context = {
        "project_name": project_name,
        "project_slug": project_slug,
        "owner": owner,
        "repo": repo,
        "repo_path": repo_path,
        "issue_number": str(issue_number or ""),
        "title": title,
        "url": url,
        "description": body,
        "comments": comments_text or "",
        "internal_notes": notes_text or "",
        "branch_name": branch_name,
    }
    return render_prompt_template(prompt_template, default_prompt, context)


class GitLabIssuesSource(TaskSource):
    id = "gitlab_issues"
    enabled = True
    base_priority = 60
    poll_interval_seconds = 10  # Check frequently; per-source intervals control actual polling

    def __init__(self) -> None:
        self._last_poll: dict[str, float] = {}

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        logger = logging.getLogger(__name__)
        drafts: list[WorkItemDraft] = []
        sources = db.list_gitlab_sources()
        now = datetime.now(timezone.utc).timestamp()
        for repo_source in sources:
            if not repo_source.enabled:
                continue
            if not repo_source.token_id:
                continue
            # Check per-source poll interval
            last_poll = self._last_poll.get(repo_source.id, 0.0)
            if now - last_poll < repo_source.poll_interval_seconds:
                continue
            self._last_poll[repo_source.id] = now
            token_record = db.get_gitlab_token(repo_source.token_id)
            if not token_record:
                continue
            try:
                issues = await self._fetch_issues(
                    token_record.token,
                    repo_source.project_path,
                    repo_source.state,
                    repo_source.labels,
                    base_url=token_record.base_url,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to fetch issues for %s: %s", repo_source.project_path, exc
                )
                continue
            logger.info("Fetched %d issues for %s", len(issues), repo_source.project_path)
            for issue in issues:
                updated_at = issue.get("updated_at") or ""
                issue_number = issue.get("iid")
                if issue_number is None:
                    continue
                work_id = (
                    f"gitlab:{repo_source.id}:{repo_source.project_path}"
                    f"#{issue_number}:{updated_at}"
                )
                drafts.append(
                    WorkItemDraft(
                        work_id=work_id,
                        priority=self.base_priority,
                        source_id=self.id,
                        checkpoint={
                            "gitlab_source_id": repo_source.id,
                            "gitlab_token_id": repo_source.token_id,
                            "project_id": repo_source.project_id,
                            "project_path": repo_source.project_path,
                            "issue_number": issue_number,
                            "title": issue.get("title"),
                            "body": issue.get("description") or "",
                            "state": issue.get("state"),
                            "web_url": issue.get("web_url"),
                            "api_url": issue.get("url"),
                            "updated_at": updated_at,
                            "labels": issue.get("labels", []),
                            "author": (issue.get("author") or {}).get("username"),
                        },
                    )
                )
        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        return GitLabIssueWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            checkpoint=record.checkpoint,
        )

    async def _fetch_issues(
        self,
        token: str,
        project_id: str,
        state: str,
        labels: list[str],
        base_url: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        state_value = (state or "open").strip().lower()
        if state_value == "open":
            state_value = "opened"
        params = {
            "state": state_value,
            "per_page": 50,
        }
        if labels:
            if isinstance(labels, str):
                labels_value = labels
            else:
                labels_value = ",".join(labels)
            params["labels"] = labels_value
        headers = {
            "Accept": "application/json",
            "PRIVATE-TOKEN": token,
            "User-Agent": "wintermute",
        }
        encoded = _encode_project_id(project_id)
        if base_url:
            api_base = base_url.rstrip("/")
            if not api_base.endswith("/api/v4"):
                api_base = f"{api_base}/api/v4"
        else:
            api_base = GITLAB_API_BASE
        url = f"{api_base}/projects/{encoded}/issues"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                # Check status before parsing JSON to avoid crashes on HTML error pages
                if response.status >= 400:
                    logging.getLogger(__name__).warning(
                        "GitLab API returned %d for %s", response.status, project_id
                    )
                    return []
                try:
                    payload = await response.json()
                except aiohttp.ContentTypeError as exc:
                    logging.getLogger(__name__).warning(
                        "GitLab API returned non-JSON response for %s: %s", project_id, exc
                    )
                    return []
                if isinstance(payload, list):
                    return payload
                return []
