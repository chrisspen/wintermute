"""GitLab API tools."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any, Optional
import urllib.parse

import aiohttp

from wintermute.db import Database
from wintermute.tools.base import Tool, ToolDefinition


GITLAB_PROVIDER = "gitlab"
GITLAB_TOKEN_NAME = "token"
GITLAB_API_BASE = os.environ.get("WINTERMUTE_GITLAB_API_BASE", "https://gitlab.com/api/v4").rstrip("/")


def _encode_project_id(project_id: str) -> str:
    return urllib.parse.quote(project_id, safe="")


class GitLabToolBase(Tool):
    db: Database
    api_base: str = GITLAB_API_BASE

    def _resolve_token(self, token_id: str) -> str:
        record = self.db.get_gitlab_token(token_id)
        if not record:
            raise ValueError("GitLab token not found")
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
            "Accept": "application/json",
            "PRIVATE-TOKEN": token,
            "User-Agent": "wintermute",
        }
        url = f"{self.api_base}{path}"
        async with aiohttp.ClientSession() as session:
            async with session.request(
                method, url, headers=headers, params=params, json=json_body
            ) as response:
                payload = await response.json()
                if response.status >= 400:
                    message = "GitLab API error"
                    if isinstance(payload, dict):
                        message = payload.get("message", message)
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
class GitLabListIssuesTool(GitLabToolBase):
    db: Database

    definition: ToolDefinition = ToolDefinition(
        name="gitlab_list_issues",
        description="List issues for a GitLab project.",
        input_schema={
            "type": "object",
            "properties": {
                "token_id": {"type": "string"},
                "project_id": {"type": "string"},
                "state": {"type": "string", "enum": ["open", "opened", "closed", "all"]},
                "labels": {"type": "array", "items": {"type": "string"}},
                "per_page": {"type": "integer"},
                "page": {"type": "integer"},
            },
            "required": ["token_id", "project_id"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        token_id = args.get("token_id")
        project_id = args.get("project_id")
        if not token_id or not project_id:
            raise ValueError("token_id and project_id are required")
        labels = args.get("labels") or []
        if isinstance(labels, str):
            labels = [value.strip() for value in labels.split(",") if value.strip()]
        state = (args.get("state") or "opened").strip().lower()
        if state == "open":
            state = "opened"
        params = {
            "state": state,
            "per_page": args.get("per_page") or 30,
            "page": args.get("page") or 1,
        }
        if labels:
            params["labels"] = ",".join(labels)
        encoded = _encode_project_id(project_id)
        return await self._request_with_token(
            "GET",
            f"/projects/{encoded}/issues",
            token_id=token_id,
            params=params,
        )


@dataclass
class GitLabGetIssueTool(GitLabToolBase):
    db: Database

    definition: ToolDefinition = ToolDefinition(
        name="gitlab_get_issue",
        description="Fetch a single GitLab issue by IID.",
        input_schema={
            "type": "object",
            "properties": {
                "token_id": {"type": "string"},
                "project_id": {"type": "string"},
                "issue_iid": {"type": "integer"},
            },
            "required": ["token_id", "project_id", "issue_iid"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        token_id = args.get("token_id")
        project_id = args.get("project_id")
        issue_iid = args.get("issue_iid")
        if not token_id or not project_id or issue_iid is None:
            raise ValueError("token_id, project_id, and issue_iid are required")
        encoded = _encode_project_id(project_id)
        return await self._request_with_token(
            "GET",
            f"/projects/{encoded}/issues/{issue_iid}",
            token_id=token_id,
        )


@dataclass
class GitLabCommentIssueTool(GitLabToolBase):
    db: Database

    definition: ToolDefinition = ToolDefinition(
        name="gitlab_comment_issue",
        description="Post a comment on a GitLab issue.",
        input_schema={
            "type": "object",
            "properties": {
                "token_id": {"type": "string"},
                "project_id": {"type": "string"},
                "issue_iid": {"type": "integer"},
                "body": {"type": "string"},
            },
            "required": ["token_id", "project_id", "issue_iid", "body"],
        },
    )

    async def __call__(self, args: dict[str, Any]) -> Any:
        token_id = args.get("token_id")
        project_id = args.get("project_id")
        issue_iid = args.get("issue_iid")
        body = args.get("body")
        if not token_id or not project_id or issue_iid is None or not body:
            raise ValueError("token_id, project_id, issue_iid, and body are required")
        encoded = _encode_project_id(project_id)
        return await self._request_with_token(
            "POST",
            f"/projects/{encoded}/issues/{issue_iid}/notes",
            token_id=token_id,
            json_body={"body": body},
        )
