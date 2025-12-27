"""GitHub API tools."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import aiohttp

from wintermute.db import Database
from wintermute.tools.base import Tool, ToolDefinition


GITHUB_PROVIDER = "github"
GITHUB_TOKEN_NAME = "token"
GITHUB_API_BASE = "https://api.github.com"


class GitHubToolBase(Tool):
    db: Database
    api_base: str = GITHUB_API_BASE

    def _resolve_token(self, token_id: str) -> str:
        record = self.db.get_github_token(token_id)
        if not record:
            raise ValueError("GitHub token not found")
        return record.token

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> Any:
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "wintermute",
        }
        url = f"{self.api_base}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, params=params, json=json_body
            ) as response:
                payload = await response.json()
                if response.status >= 400:
                    message = payload.get("message", "GitHub API error")
                    raise ValueError(f"{response.status} {message}")
                return payload

    async def _request_with_token(
        self,
        method: str,
        path: str,
        *,
        token_id: str,
        params: Optional[dict[str, Any]] = None,
        json_body: Optional[dict[str, Any]] = None,
    ) -> Any:
        token = self._resolve_token(token_id)
        return await self._request(
            method,
            path,
            token=token,
            params=params,
            json_body=json_body,
        )


@dataclass
class GitHubListIssuesTool(GitHubToolBase):
    db: Database

    definition: ToolDefinition = ToolDefinition(
        name="github_list_issues",
        description="List issues for a GitHub repo.",
        input_schema={
            "type": "object",
            "properties": {
                "token_id": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "closed", "all"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "per_page": {"type": "integer"},
                "page": {"type": "integer"},
            },
            "required": ["token_id", "owner", "repo"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        token_id = args.get("token_id")
        owner = args.get("owner")
        repo = args.get("repo")
        if not token_id or not owner or not repo:
            raise ValueError("token_id, owner, and repo are required")
        labels = args.get("labels") or []
        if isinstance(labels, str):
            labels = [value.strip() for value in labels.split(",") if value.strip()]
        params = {
            "state": args.get("state") or "open",
            "per_page": args.get("per_page") or 30,
            "page": args.get("page") or 1,
        }
        if labels:
            params["labels"] = ",".join(labels)
        return await self._request_with_token(
            "GET",
            f"/repos/{owner}/{repo}/issues",
            token_id=token_id,
            params=params,
        )


@dataclass
class GitHubGetIssueTool(GitHubToolBase):
    db: Database

    definition: ToolDefinition = ToolDefinition(
        name="github_get_issue",
        description="Fetch a single GitHub issue by number.",
        input_schema={
            "type": "object",
            "properties": {
                "token_id": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
            },
            "required": ["token_id", "owner", "repo", "issue_number"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        token_id = args.get("token_id")
        owner = args.get("owner")
        repo = args.get("repo")
        issue_number = args.get("issue_number")
        if not token_id or not owner or not repo or issue_number is None:
            raise ValueError("token_id, owner, repo, and issue_number are required")
        return await self._request_with_token(
            "GET",
            f"/repos/{owner}/{repo}/issues/{issue_number}",
            token_id=token_id,
        )


@dataclass
class GitHubCommentIssueTool(GitHubToolBase):
    db: Database

    definition: ToolDefinition = ToolDefinition(
        name="github_comment_issue",
        description="Post a comment on a GitHub issue.",
        input_schema={
            "type": "object",
            "properties": {
                "token_id": {"type": "string"},
                "owner": {"type": "string"},
                "repo": {"type": "string"},
                "issue_number": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["token_id", "owner", "repo", "issue_number", "body"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        token_id = args.get("token_id")
        owner = args.get("owner")
        repo = args.get("repo")
        issue_number = args.get("issue_number")
        body = args.get("body")
        if not token_id or not owner or not repo or issue_number is None or not body:
            raise ValueError("token_id, owner, repo, issue_number, and body are required")
        return await self._request_with_token(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            token_id=token_id,
            json_body={"body": body},
        )
