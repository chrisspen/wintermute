"""Dispatch approved public comments to GitHub or GitLab."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from wintermute.services.database import AsyncDatabase
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft
from wintermute.tickets import parse_issue_ticket


@dataclass
class CommentDispatchWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    comment_id: str

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
        comment = await ctx.db.get_comment(self.comment_id)
        if not comment:
            return
        if not comment.public or not comment.approved or comment.sent:
            return
        provider, source_id, issue_number = parse_issue_ticket(comment.ticket_id)
        if comment.issue_number is not None:
            issue_number = comment.issue_number
        if not source_id or issue_number is None or not provider:
            logger.warning("Comment %s missing source or issue number", comment.id)
            return
        if provider == "github":
            source = await ctx.db.get_github_source(source_id)
            if not source or not source.token_id:
                logger.warning("Comment %s missing GitHub source/token", comment.id)
                return
            tool = ctx.tools.get("github_comment_issue")
            if not tool:
                logger.warning("GitHub comment tool not available")
                return
            await ctx.tools.call(
                "github_comment_issue",
                {
                    "token_id": source.token_id,
                    "owner": source.owner,
                    "repo": source.repo,
                    "issue_number": issue_number,
                    "body": comment.body,
                },
            )
        elif provider == "gitlab":
            source = await ctx.db.get_gitlab_source(source_id)
            if not source or not source.token_id:
                logger.warning("Comment %s missing GitLab source/token", comment.id)
                return
            tool = ctx.tools.get("gitlab_comment_issue")
            if not tool:
                logger.warning("GitLab comment tool not available")
                return
            await ctx.tools.call(
                "gitlab_comment_issue",
                {
                    "token_id": source.token_id,
                    "project_id": source.project_path,
                    "issue_iid": issue_number,
                    "body": comment.body,
                },
            )
        else:
            logger.warning("Comment %s provider unsupported", comment.id)
            return
        await ctx.db.mark_comment_sent(comment.id)


class CommentDispatchSource(TaskSource):
    id = "comment_dispatch"
    enabled = True
    base_priority = 70
    poll_interval_seconds = 5

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: AsyncDatabase = ctx["db"]
        source = await db.get_task_source(self.id)
        if source and not source.enabled:
            return []
        comments = await db.list_pending_comments()
        drafts: list[WorkItemDraft] = []
        for comment in comments:
            drafts.append(
                WorkItemDraft(
                    work_id=f"comment:{comment.id}:{comment.updated_at}",
                    priority=self.base_priority,
                    source_id=self.id,
                    checkpoint={"comment_id": comment.id},
                )
            )
        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        comment_id = record.checkpoint["comment_id"]
        return CommentDispatchWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            comment_id=comment_id,
        )
