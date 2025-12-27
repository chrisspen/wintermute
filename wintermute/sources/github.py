"""GitHub issues TaskSource."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

import aiohttp

from wintermute.db import Database
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft


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
