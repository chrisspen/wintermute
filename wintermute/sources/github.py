"""GitHub issues TaskSource."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

import aiohttp

from wintermute.db import Database
from wintermute.runner import build_ssh_spec, ensure_repo, send_input, start_session
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft, WorkItemBlocked


GITHUB_API_BASE = "https://api.github.com"


def _tool_schema(ctx: WorkItemContext) -> list[dict[str, Any]]:
    return [asdict(definition) for definition in ctx.tools.definitions()]


@dataclass
class GitHubIssueWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]

    async def resume(self, ctx: WorkItemContext) -> None:
        source_id = self.checkpoint.get("github_source_id")
        source = ctx.db.get_github_source(source_id) if source_id else None
        if source and source.auto_start:
            await self._auto_start(ctx, source)
            return
        await self._llm_decide(ctx)

    async def _llm_decide(self, ctx: WorkItemContext) -> None:
        decision = ctx.executor.decide_next_action(
            state=dict(self.checkpoint),
            observation={"issue": dict(self.checkpoint)},
            tool_schema=_tool_schema(ctx),
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

    async def _auto_start(self, ctx: WorkItemContext, source: Any) -> None:
        project = ctx.db.get_project(source.project_id)
        if not project:
            return
        if not source.agent_id:
            await self._notify(ctx, project.slack_channel_id, "GitHub source missing agent assignment.")
            return
        agent = ctx.db.get_agent(source.agent_id)
        if not agent:
            await self._notify(ctx, project.slack_channel_id, "GitHub source agent not found.")
            return
        project_vm = ctx.db.get_project_vm_for_project(project.id)
        if not project_vm:
            await self._notify(ctx, project.slack_channel_id, "No VM mapping configured for this project.")
            return
        vm = ctx.db.get_vm_target(project_vm.vm_target_id)
        if not vm:
            await self._notify(ctx, project.slack_channel_id, "VM target not found for this project.")
            return
        issue_number = self.checkpoint.get("issue_number")
        if issue_number is None:
            return
        ticket_id = f"github:{source.id}:{issue_number}"
        existing_ticket = ctx.db.get_ticket(ticket_id)
        issue_title = str(self.checkpoint.get("title") or "")
        issue_body = str(self.checkpoint.get("body") or "")
        if not existing_ticket:
            ctx.db.insert_ticket(
                ticket_id=ticket_id,
                project_id=project.id,
                title=issue_title or f"GitHub issue #{issue_number}",
                description=issue_body,
                assigned_to=agent.name,
                estimate=None,
                status="open",
            )
        else:
            ctx.db.update_ticket(
                ticket_id,
                title=issue_title or existing_ticket.title,
                description=issue_body or existing_ticket.description,
            )
        if ctx.db.get_session_by_ticket(ticket_id):
            return
        running = ctx.db.list_sessions(project_id=project.id, status="running")
        for session in running:
            if session.project_vm_id == project_vm.id:
                raise WorkItemBlocked("Project session already running", delay_seconds=60)
        spec = build_ssh_spec(vm, agent.required_ssh_options)
        repo_path = ensure_repo(spec, project_vm)
        if not repo_path:
            await self._notify(ctx, project.slack_channel_id, "Repository not configured for this project VM.")
            return
        issue_url = self.checkpoint.get("html_url") or ""
        title_line = f"Issue #{issue_number}: {issue_title}".strip()
        thread_ts = await self._notify(
            ctx,
            project.slack_channel_id,
            f"{title_line}\n{issue_url}\nStarting agent session...",
        )
        session_id = f"{project.slug}-{agent.slug}-issue-{issue_number}"
        ctx.db.insert_session(
            session_id=session_id,
            project_id=project.id,
            project_vm_id=project_vm.id,
            agent_id=agent.id,
            ticket_id=ticket_id,
            status="running",
            repo_path=repo_path,
            thread_ts=thread_ts,
        )
        start_session(spec, session_id, agent, repo_path)
        session = ctx.db.get_session(session_id)
        if session:
            prompt = _issue_prompt(self.checkpoint, source.owner, source.repo)
            send_input(spec, session, prompt)
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


def _issue_prompt(issue: dict[str, Any], owner: str, repo: str) -> str:
    issue_number = issue.get("issue_number")
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    url = issue.get("html_url") or ""
    return (
        "You are working on a GitHub issue.\n"
        f"Repo: {owner}/{repo}\n"
        f"Issue #{issue_number}: {title}\n"
        f"URL: {url}\n\n"
        "Instructions:\n"
        "- Create a new branch from the default branch (main/master).\n"
        f"- Branch name: issue-{issue_number} (or similar).\n"
        "- Implement the fix, commit changes with a clear message, and push the branch.\n"
        "- Post status updates and questions in the Slack thread for this issue.\n\n"
        "Issue description:\n"
        f"{body}\n"
    )


class GitHubIssuesSource(TaskSource):
    id = "github_issues"
    enabled = False
    base_priority = 60
    poll_interval_seconds = 60

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        source = db.get_task_source(self.id)
        if not source or not source.enabled:
            return []
        drafts: list[WorkItemDraft] = []
        sources = [row for row in db.list_github_sources() if row.enabled]
        for repo_source in sources:
            if not repo_source.token_id:
                continue
            token_record = db.get_github_token(repo_source.token_id)
            if not token_record:
                continue
            issues = await self._fetch_issues(
                token_record.token,
                repo_source.owner,
                repo_source.repo,
                repo_source.state,
                repo_source.labels,
            )
            for issue in issues:
                if issue.get("pull_request"):
                    continue
                updated_at = issue.get("updated_at") or ""
                issue_number = issue.get("number")
                if issue_number is None:
                    continue
                work_id = (
                    f"github:{repo_source.id}:{repo_source.owner}/{repo_source.repo}"
                    f"#{issue_number}:{updated_at}"
                )
                drafts.append(
                    WorkItemDraft(
                        work_id=work_id,
                        priority=source.base_priority,
                        source_id=self.id,
                        checkpoint={
                            "github_source_id": repo_source.id,
                            "github_token_id": repo_source.token_id,
                            "project_id": repo_source.project_id,
                            "owner": repo_source.owner,
                            "repo": repo_source.repo,
                            "issue_number": issue_number,
                            "title": issue.get("title"),
                            "body": issue.get("body") or "",
                            "state": issue.get("state"),
                            "html_url": issue.get("html_url"),
                            "api_url": issue.get("url"),
                            "updated_at": updated_at,
                            "labels": [label.get("name") for label in issue.get("labels", [])],
                            "author": (issue.get("user") or {}).get("login"),
                        },
                    )
                )
        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        return GitHubIssueWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            checkpoint=record.checkpoint,
        )

    async def _fetch_issues(
        self,
        token: str,
        owner: str,
        repo: str,
        state: str,
        labels: list[str],
    ) -> list[dict[str, Any]]:
        params = {
            "state": state,
            "per_page": 50,
        }
        if labels:
            if isinstance(labels, str):
                labels_value = labels
            else:
                labels_value = ",".join(labels)
            params["labels"] = labels_value
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "wintermute",
        }
        url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/issues"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                payload = await response.json()
                if response.status >= 400:
                    return []
                if isinstance(payload, list):
                    return payload
                return []
