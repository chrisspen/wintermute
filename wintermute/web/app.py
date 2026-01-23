"""FastAPI admin console."""

from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import signal
import subprocess
import sys
import time
import hashlib
import hmac
import os
import re
import secrets
import uuid
import urllib.parse
from datetime import datetime, timezone
from dataclasses import asdict
from typing import Any, Optional

import aiohttp
from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi import Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from slack_sdk.web.client import WebClient
from sqlalchemy.exc import IntegrityError

from wintermute.db import Database, utc_now
from wintermute.prompts import DEFAULT_PROJECT_PROMPT_TEMPLATE, render_prompt_template
from wintermute.mcp_client import close_mcp_process
from wintermute.runner import (
    build_ssh_spec,
    build_ssh_spec_with_options,
    check_vm_memory_available,
    configure_git_push_auth,
    ensure_repo,
    ensure_vm_tools,
    is_codex_command,
    is_session_running,
    parse_ssh_options,
    prepare_issue_branch,
    prepare_local_ticket_branch,
    prepare_ticket_branch,
    send_input,
    set_codex_trust,
    start_session,
    stop_session,
    strip_port_forwards,
)
from wintermute.sources.github import GitHubIssuesSource, _fetch_issue_comments, _issue_prompt
from wintermute.sources.gitlab import (
    GitLabIssuesSource,
    _fetch_issue_comments as _fetch_gitlab_issue_comments,
    _issue_prompt as _gitlab_issue_prompt,
)
from wintermute.sources.comment_dispatch import CommentDispatchSource
from wintermute.sources.slack import (
    SLACK_APP_TOKEN_NAME,
    SLACK_BOT_TOKEN_NAME,
    SLACK_PROVIDER,
    SlackSource,
)
from wintermute.sources.tickets import TicketAutoStartSource
from wintermute.sources.standup import StandupSource
from wintermute.tickets import parse_issue_ticket
from wintermute.tools.github import GITHUB_PROVIDER, GITHUB_TOKEN_NAME

# Badge cache: {project_id: (status, timestamp)}
_badge_cache: dict[str, tuple[str, float]] = {}
BADGE_CACHE_TTL = 300 # 5 minutes


class TaskSourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    base_priority: Optional[int] = None
    poll_interval_seconds: Optional[int] = None
    config: Optional[dict[str, Any]] = None


class CredentialCreate(BaseModel):
    name: str
    provider: str
    reference: str
    note: Optional[str] = None


class WorkItemStatusUpdate(BaseModel):
    status: str
    run_after: Optional[str] = None


API_PERMISSION_MODELS = [
    {
        "key": "admin",
        "label": "Admin"
    },
    {
        "key": "agents",
        "label": "Agents"
    },
    {
        "key": "agent_responses",
        "label": "Agent Responses"
    },
    {
        "key": "comments",
        "label": "Comments"
    },
    {
        "key": "credentials",
        "label": "Credentials"
    },
    {
        "key": "github_sources",
        "label": "GitHub Sources"
    },
    {
        "key": "github_tokens",
        "label": "GitHub Tokens"
    },
    {
        "key": "gitlab_sources",
        "label": "GitLab Sources"
    },
    {
        "key": "gitlab_tokens",
        "label": "GitLab Tokens"
    },
    {
        "key": "projects",
        "label": "Projects"
    },
    {
        "key": "repo_resources",
        "label": "Repo Resources"
    },
    {
        "key": "sessions",
        "label": "Agent Sessions"
    },
    {
        "key": "supervisor_state",
        "label": "Supervisor State"
    },
    {
        "key": "task_sources",
        "label": "Task Sources"
    },
    {
        "key": "tickets",
        "label": "Tickets"
    },
    {
        "key": "users",
        "label": "Users"
    },
    {
        "key": "vms",
        "label": "VM Targets"
    },
    {
        "key": "work_items",
        "label": "Work Items"
    },
]
API_PERMISSION_ACTIONS = ["create", "read", "update", "delete"]

LIST_TABLE_CONFIGS: dict[str, dict[str, Any]] = {
    "tickets": {
        "default": ["title", "project_id", "status"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "title",
                "label": "Ticket"
            },
            {
                "key": "project_id",
                "label": "Project"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "assigned_to",
                "label": "Assignee"
            },
            {
                "key": "created_by_id",
                "label": "Created By"
            },
            {
                "key": "estimate",
                "label": "Estimate"
            },
            {
                "key": "status",
                "label": "Status"
            },
            {
                "key": "auto_start",
                "label": "Auto Start"
            },
            {
                "key": "source_url",
                "label": "Source"
            },
            {
                "key": "description",
                "label": "Description"
            },
            {
                "key": "internal_notes",
                "label": "Internal Notes"
            },
            {
                "key": "github_comments_json",
                "label": "GitHub Comments"
            },
            {
                "key": "github_comments_fetched_at",
                "label": "GitHub Cached At"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "projects": {
        "default": ["name", "build_status", "slug", "slack_channel_id", "actions"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "Project"
            },
            {
                "key": "build_status",
                "label": "Build Status"
            },
            {
                "key": "slug",
                "label": "Slug"
            },
            {
                "key": "slack_channel_id",
                "label": "Slack Channel"
            },
            {
                "key": "max_repo_resources",
                "label": "Max Repo Resources"
            },
            {
                "key": "prompt_template",
                "label": "Prompt Template"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "actions",
                "label": "Actions"
            },
        ],
    },
    "vms": {
        "default": ["name", "host", "user"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "VM"
            },
            {
                "key": "host",
                "label": "Host"
            },
            {
                "key": "user",
                "label": "User"
            },
            {
                "key": "port",
                "label": "Port"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "agents": {
        "default": ["name", "slug", "session_mode"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "Agent"
            },
            {
                "key": "slug",
                "label": "Slug"
            },
            {
                "key": "session_mode",
                "label": "Session Mode"
            },
            {
                "key": "vm_target_id",
                "label": "VM"
            },
            {
                "key": "command",
                "label": "Command"
            },
            {
                "key": "required_ssh_options",
                "label": "SSH Options"
            },
            {
                "key": "env_vars",
                "label": "Env Vars"
            },
            {
                "key": "mcp_config",
                "label": "MCP Config"
            },
            {
                "key": "trust_level",
                "label": "Trust Level"
            },
            {
                "key": "input_echo_prefix",
                "label": "Input Echo Prefix"
            },
            {
                "key": "response_prefix",
                "label": "Response Prefix"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
        "bulk_actions": [
            {
                "key": "clone",
                "label": "Clone"
            },
        ],
    },
    "api_tokens": {
        "default": ["name", "permissions", "created_at"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "Token Name"
            },
            {
                "key": "token",
                "label": "Token",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "permissions",
                "label": "Permissions"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "comments": {
        "default": ["body", "project_id", "public", "approved", "sent"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "body",
                "label": "Comment"
            },
            {
                "key": "ticket_id",
                "label": "Ticket"
            },
            {
                "key": "session_id",
                "label": "Session"
            },
            {
                "key": "project_id",
                "label": "Project"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "author",
                "label": "Author"
            },
            {
                "key": "source_id",
                "label": "Source"
            },
            {
                "key": "issue_number",
                "label": "Issue #"
            },
            {
                "key": "public",
                "label": "Public"
            },
            {
                "key": "approved",
                "label": "Approved"
            },
            {
                "key": "sent",
                "label": "Sent"
            },
            {
                "key": "sent_at",
                "label": "Sent At",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "remote_tokens": {
        "default": ["provider", "note", "user_login"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "provider",
                "label": "Provider"
            },
            {
                "key": "note",
                "label": "Note"
            },
            {
                "key": "token",
                "label": "Token",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "user_id",
                "label": "User ID"
            },
            {
                "key": "user_login",
                "label": "User Login"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "issue_sources": {
        "default": ["provider", "project_id", "repo", "enabled", "poll_interval_seconds"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "provider",
                "label": "Provider"
            },
            {
                "key": "project_id",
                "label": "Project"
            },
            {
                "key": "repo",
                "label": "Repo"
            },
            {
                "key": "token_id",
                "label": "Token"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "state",
                "label": "State"
            },
            {
                "key": "labels",
                "label": "Labels"
            },
            {
                "key": "enabled",
                "label": "Enabled"
            },
            {
                "key": "auto_start",
                "label": "Auto Start"
            },
            {
                "key": "poll_interval_seconds",
                "label": "Poll Interval"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "agent_responses": {
        "default": ["agent_id", "pattern", "response"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "pattern",
                "label": "Pattern"
            },
            {
                "key": "response",
                "label": "Response"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "repo_resources": {
        "default": ["path", "project_id", "status"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "path",
                "label": "Path"
            },
            {
                "key": "project_id",
                "label": "Project"
            },
            {
                "key": "repo_mode",
                "label": "Repo Mode"
            },
            {
                "key": "status",
                "label": "Status"
            },
            {
                "key": "session_id",
                "label": "Session"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "last_used_at",
                "label": "Last Used",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "work_items": {
        "default": ["work_id", "source_id", "status", "priority"],
        "columns": [
            {
                "key": "work_id",
                "label": "Work ID"
            },
            {
                "key": "source_id",
                "label": "Source"
            },
            {
                "key": "priority",
                "label": "Priority"
            },
            {
                "key": "status",
                "label": "Status"
            },
            {
                "key": "attempts",
                "label": "Attempts"
            },
            {
                "key": "run_after",
                "label": "Run After",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "last_error",
                "label": "Last Error"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "sessions": {
        "default": ["id", "project_id", "agent_id", "status"],
        "columns": [
            {
                "key": "id",
                "label": "Session ID"
            },
            {
                "key": "project_id",
                "label": "Project"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "ticket_id",
                "label": "Ticket"
            },
            {
                "key": "status",
                "label": "Status"
            },
            {
                "key": "repo_path",
                "label": "Repo Path"
            },
            {
                "key": "thread_ts",
                "label": "Thread"
            },
            {
                "key": "mcp_conversation_id",
                "label": "MCP Conv ID"
            },
            {
                "key": "claude_session_id",
                "label": "Claude Session"
            },
            {
                "key": "last_output_at",
                "label": "Last Output",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "sprints": {
        "default": ["name", "start_date", "end_date", "status"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "Sprint"
            },
            {
                "key": "start_date",
                "label": "Start Date"
            },
            {
                "key": "end_date",
                "label": "End Date"
            },
            {
                "key": "enabled",
                "label": "Auto-Cycle"
            },
            {
                "key": "status",
                "label": "Status"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "session_file_configs": {
        "default": ["name", "description"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "Name"
            },
            {
                "key": "description",
                "label": "Description"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "metric_definitions": {
        "default": ["metric_type", "recording_frequency_minutes", "enabled"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "metric_type",
                "label": "Metric Type"
            },
            {
                "key": "recording_frequency_minutes",
                "label": "Frequency (min)"
            },
            {
                "key": "enabled",
                "label": "Enabled"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "agent_metrics_logs": {
        "default": ["agent_id", "metric_type", "value", "recorded_at"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "agent_id",
                "label": "Agent"
            },
            {
                "key": "metric_type",
                "label": "Metric Type"
            },
            {
                "key": "value",
                "label": "Value"
            },
            {
                "key": "recorded_at",
                "label": "Recorded At",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
    },
    "session_file_configs": {
        "default": ["name", "description"],
        "columns": [
            {
                "key": "id",
                "label": "ID",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "name",
                "label": "Name"
            },
            {
                "key": "description",
                "label": "Description"
            },
            {
                "key": "created_at",
                "label": "Created",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
            {
                "key": "updated_at",
                "label": "Updated",
                "cell_class": "font-mono text-xs text-slate-500 dark:text-slate-400"
            },
        ],
        "bulk_actions": [
            {
                "key": "clone",
                "label": "Clone"
            },
        ],
    },
}


def _hash_password(password: str, salt: bytes) -> str:
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=2**14,
        r=8,
        p=1,
    )
    return base64.b64encode(derived).decode("ascii")


def _verify_password(password: str, salt_b64: str, stored_hash: str) -> bool:
    salt = base64.b64decode(salt_b64.encode("ascii"))
    candidate = _hash_password(password, salt)
    return hmac.compare_digest(candidate, stored_hash)


async def _fetch_github_issue_comments(token: str, owner: str, repo: str, issue_number: int) -> tuple[list[dict[str, Any]], Optional[str]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "wintermute",
    }
    comments: list[dict[str, Any]] = []
    page = 1
    while page <= 3:
        params = {"per_page": 100, "page": page}
        url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/comments"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, params=params) as response:
                payload = await response.json()
                if response.status >= 400:
                    message = payload.get("message") if isinstance(payload, dict) else str(payload)
                    return [], f"{response.status} {message}"
                if not isinstance(payload, list):
                    return [], "Unexpected response format"
                if not payload:
                    break
                comments.extend(payload)
                if len(payload) < 100:
                    break
        page += 1
    return comments, None


def _github_cache_seconds() -> int:
    raw = os.environ.get("WINTERMUTE_GITHUB_COMMENT_CACHE_SECONDS", "300")
    try:
        value = int(raw)
    except ValueError:
        value = 300
    return max(value, 0)


def _load_cached_github_comments(ticket: Any) -> list[dict[str, Any]]:
    raw = getattr(ticket, "github_comments_json", None)
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return payload
    return []


async def _get_github_comments_cached(
    db: Database,
    ticket: Any,
    token: str,
    owner: str,
    repo: str,
    issue_number: int,
    force_refresh: bool = False,
) -> tuple[list[dict[str, Any]], Optional[str], bool]:
    cached = _load_cached_github_comments(ticket)
    fetched_at = getattr(ticket, "github_comments_fetched_at", None)
    if not force_refresh and cached and fetched_at:
        try:
            fetched = datetime.fromisoformat(fetched_at)
        except ValueError:
            fetched = None
        if fetched:
            age = (datetime.now(timezone.utc) - fetched).total_seconds()
            if age < _github_cache_seconds():
                return cached, None, True
    comments, error = await _fetch_github_issue_comments(token, owner, repo, issue_number)
    if comments:
        db.update_ticket(
            ticket.id,
            github_comments_json=json.dumps(comments),
            github_comments_fetched_at=utc_now(),
        )
        return comments, None, False
    if cached:
        return cached, error or "GitHub fetch failed; using cached comments", True
    return [], error, False


def _parse_github_ticket(ticket_id: str) -> tuple[Optional[str], Optional[int]]:
    provider, source_id, issue_number = parse_issue_ticket(ticket_id)
    if provider != "github":
        return None, None
    return source_id, issue_number


def _parse_gitlab_ticket(ticket_id: str) -> tuple[Optional[str], Optional[int]]:
    provider, source_id, issue_number = parse_issue_ticket(ticket_id)
    if provider != "gitlab":
        return None, None
    return source_id, issue_number


def _require_login(request: Request) -> str:
    username = request.session.get("user")
    if not username:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return username


def _parse_channels(raw: str) -> list[str]:
    channels = []
    for item in raw.replace("\n", ",").split(","):
        cleaned = item.strip()
        if cleaned:
            channels.append(cleaned)
    return channels


def _parse_labels(raw: str) -> list[str]:
    labels = []
    for item in raw.replace("\n", ",").split(","):
        cleaned = item.strip()
        if cleaned:
            labels.append(cleaned)
    return labels


def _normalize_source_repo(provider: str, raw: str) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None
    provider = provider.strip().lower()
    if "://" in value:
        parsed = urllib.parse.urlparse(value)
        path = parsed.path.strip("/")
    else:
        path = value.strip("/")
        if "github.com/" in value:
            path = value.split("github.com/", 1)[1].strip("/")
        if "gitlab.com/" in value:
            path = value.split("gitlab.com/", 1)[1].strip("/")
    if "/-/" in path:
        path = path.split("/-/", 1)[0]
    if path.endswith(".git"):
        path = path[:-4]
    if provider == "github":
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
        return path
    return path


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "n/a"
    return str(value)


def _truncate_text(value: Optional[str], limit: int = 80) -> str:
    text = _display_value(value)
    if text == "n/a":
        return text
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[:limit - 3] + "..."


def _format_timestamp(value: Optional[str]) -> str:
    if not value:
        return "n/a"
    cleaned = value.replace("T", " ")
    if "." in cleaned:
        cleaned = cleaned.split(".", 1)[0]
    return cleaned


def _mask_token(value: Optional[str]) -> str:
    if not value:
        return "n/a"
    if len(value) <= 12:
        return value
    return f"{value[:4]}...{value[-4:]}"


def _format_permissions(permissions: dict[str, dict[str, bool]]) -> str:
    if not permissions:
        return "n/a"
    models = [key for key, actions in permissions.items() if any(actions.values())]
    if not models:
        return "n/a"
    models.sort()
    return _truncate_text(", ".join(models), 60)


def _format_github_comments(raw: Optional[str]) -> str:
    if not raw:
        return "n/a"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return "cached"
    if isinstance(payload, list):
        return f"{len(payload)} comments"
    return "cached"


def _format_url(value: Optional[str], limit: int = 48) -> str:
    if not value:
        return "n/a"
    trimmed = value.replace("https://", "").replace("http://", "")
    return _truncate_text(trimmed, limit)


def _resolve_table_columns(database: Database, user: str, model: str, available_keys: list[str], default_keys: list[str]) -> list[str]:
    selected: list[str] = []
    user_record = database.get_user(user)
    if user_record:
        pref = database.get_column_preferences(user_record.id, model)
        if pref:
            selected = [key for key in pref.columns if key in available_keys]
    if not selected:
        selected = [key for key in default_keys if key in available_keys]
    if not selected and available_keys:
        selected = [available_keys[0]]
    return selected


def _safe_return_to(request: Request, fallback: str) -> str:
    return_to = request.url.path
    if request.url.query:
        return_to = f"{return_to}?{request.url.query}"
    if not return_to.startswith("/ui"):
        return fallback
    return return_to


def _build_ticket_rows(
    tickets: list[Any],
    project_lookup: dict[str, str],
    agent_lookup: dict[str, str],
    user_lookup: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    user_lookup = user_lookup or {}
    for ticket in tickets:
        project_name = project_lookup.get(ticket.project_id, ticket.project_id)
        agent_name = agent_lookup.get(ticket.agent_id, ticket.agent_id) if ticket.agent_id else None
        created_by_name = user_lookup.get(ticket.created_by_id) if ticket.created_by_id else None
        cells = {
            "id": {
                "text": _display_value(ticket.id)
            },
            "title": {
                "text": _display_value(ticket.title),
                "href": f"/ui/tickets/{ticket.id}/edit"
            },
            "project_id": {
                "text": _display_value(project_name),
                "href": f"/ui/projects/{ticket.project_id}/edit" if ticket.project_id else None,
            },
            "agent_id": {
                "text": _display_value(agent_name),
                "href": f"/ui/agents/{ticket.agent_id}/edit" if ticket.agent_id else None,
            },
            "assigned_to": {
                "text": _display_value(ticket.assigned_to)
            },
            "created_by_id": {
                "text": _display_value(created_by_name)
            },
            "estimate": {
                "text": _display_value(ticket.estimate)
            },
            "status": {
                "text": _display_value(ticket.status)
            },
            "auto_start": {
                "text": "yes" if ticket.auto_start else "no"
            },
            "source_url": {
                "text": _format_url(ticket.source_url),
                "href": ticket.source_url,
                "external": True,
            },
            "description": {
                "text": _truncate_text(ticket.description, 80)
            },
            "internal_notes": {
                "text": _truncate_text(ticket.internal_notes, 80)
            },
            "github_comments_json": {
                "text": _format_github_comments(ticket.github_comments_json)
            },
            "github_comments_fetched_at": {
                "text": _format_timestamp(ticket.github_comments_fetched_at)
            },
            "created_at": {
                "text": _format_timestamp(ticket.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(ticket.updated_at)
            },
        }
        rows.append({"id": ticket.id, "cells": cells})
    return rows


def _build_project_rows(projects: list[Any], database: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for project in projects:
        # Build URLs for template to render
        repo_url = None
        badge_url = None
        badge_link = None
        provider = project.provider
        if provider and project.source_repo:
            branch = project.master_branch_name or "master"
            if provider == "github":
                repo_url = f"https://github.com/{project.source_repo}"
                badge_url = f"https://github.com/{project.source_repo}/actions/workflows/ci.yml/badge.svg?branch={branch}"
                badge_link = f"https://github.com/{project.source_repo}/actions"
            elif provider == "gitlab":
                gitlab_base = "https://gitlab.com"
                if project.source_token_id:
                    token = database.get_remote_token(project.source_token_id)
                    if token and token.base_url:
                        gitlab_base = token.base_url.rstrip("/")
                repo_url = f"{gitlab_base}/{project.source_repo}"
                # Use proxy endpoint for GitLab badges (avoids referrer blocking)
                badge_url = f"/badges/projects/{project.id}"
                badge_link = f"{gitlab_base}/{project.source_repo}/-/pipelines"
            # Use custom badge URL if set (direct URL)
            if project.build_status_image_url:
                badge_url = project.build_status_image_url
        cells = {
            "id": {
                "text": _display_value(project.id)
            },
            "name": {
                "text": _display_value(project.name),
                "href": f"/ui/projects/{project.id}/edit",
                "provider": provider,
                "repo_url": repo_url,
            },
            "build_status": {
                "text": "",
                "badge_url": badge_url,
                "badge_link": badge_link,
            },
            "slug": {
                "text": _display_value(project.slug)
            },
            "slack_channel_id": {
                "text": _display_value(project.slack_channel_id)
            },
            "max_repo_resources": {
                "text": _display_value(project.max_repo_resources)
            },
            "prompt_template": {
                "text": _truncate_text(project.prompt_template, 80)
            },
            "created_at": {
                "text": _format_timestamp(project.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(project.updated_at)
            },
            "actions": {
                "text": "Create Ticket",
                "href": f"/ui/tickets/create?project_id={project.id}"
            },
        }
        rows.append({"id": project.id, "cells": cells})
    return rows


def _build_vm_rows(vm_targets: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for vm in vm_targets:
        cells = {
            "id": {
                "text": _display_value(vm.id)
            },
            "name": {
                "text": _display_value(vm.name),
                "href": f"/ui/vms/{vm.id}/edit"
            },
            "host": {
                "text": _display_value(vm.host)
            },
            "user": {
                "text": _display_value(vm.user)
            },
            "port": {
                "text": _display_value(vm.port)
            },
            "created_at": {
                "text": _format_timestamp(vm.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(vm.updated_at)
            },
        }
        rows.append({"id": vm.id, "cells": cells})
    return rows


def _build_agent_rows(agents: list[Any], vm_lookup: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for agent in agents:
        cells = {
            "id": {
                "text": _display_value(agent.id)
            },
            "name": {
                "text": _display_value(agent.name),
                "href": f"/ui/agents/{agent.id}/edit"
            },
            "slug": {
                "text": _display_value(agent.slug)
            },
            "session_mode": {
                "text": _display_value(agent.session_mode)
            },
            "vm_target_id": {
                "text": vm_lookup.get(agent.vm_target_id, agent.vm_target_id or "—")
            },
            "command": {
                "text": _truncate_text(agent.command, 80)
            },
            "required_ssh_options": {
                "text": _truncate_text(agent.required_ssh_options, 80)
            },
            "env_vars": {
                "text": _truncate_text(agent.env_vars, 80)
            },
            "mcp_config": {
                "text": _truncate_text(agent.mcp_config, 80)
            },
            "trust_level": {
                "text": _display_value(agent.trust_level)
            },
            "input_echo_prefix": {
                "text": _truncate_text(agent.input_echo_prefix, 80)
            },
            "response_prefix": {
                "text": _truncate_text(agent.response_prefix, 80)
            },
            "created_at": {
                "text": _format_timestamp(agent.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(agent.updated_at)
            },
        }
        rows.append({"id": agent.id, "cells": cells})
    return rows


def _build_api_token_rows(api_tokens: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for token in api_tokens:
        cells = {
            "id": {
                "text": _display_value(token.id)
            },
            "name": {
                "text": _display_value(token.name),
                "href": f"/ui/api-tokens/{token.id}/edit"
            },
            "token": {
                "text": _mask_token(token.token)
            },
            "permissions": {
                "text": _format_permissions(token.permissions)
            },
            "created_at": {
                "text": _format_timestamp(token.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(token.updated_at)
            },
        }
        rows.append({"id": token.id, "cells": cells})
    return rows


def _build_sprint_rows(sprints: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sprint in sprints:
        cells = {
            "id": {
                "text": _display_value(sprint.id)
            },
            "name": {
                "text": _display_value(sprint.name),
                "href": f"/ui/sprints/{sprint.id}/edit"
            },
            "start_date": {
                "text": sprint.start_date[:10] if sprint.start_date else "—"
            },
            "end_date": {
                "text": sprint.end_date[:10] if sprint.end_date else "—"
            },
            "enabled": {
                "text": "Yes" if sprint.enabled else "No"
            },
            "status": {
                "text": _display_value(sprint.status)
            },
            "created_at": {
                "text": _format_timestamp(sprint.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(sprint.updated_at)
            },
        }
        rows.append({"id": sprint.id, "cells": cells})
    return rows


def _build_comment_rows(comments: list[Any], project_lookup: dict[str, str], agent_lookup: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for comment in comments:
        project_name = project_lookup.get(comment.project_id, comment.project_id) if comment.project_id else None
        agent_name = agent_lookup.get(comment.agent_id, comment.agent_id) if comment.agent_id else None
        cells = {
            "id": {
                "text": _display_value(comment.id)
            },
            "body": {
                "text": _truncate_text(comment.body, 80),
                "href": f"/ui/comments/{comment.id}/edit"
            },
            "ticket_id": {
                "text": _display_value(comment.ticket_id)
            },
            "session_id": {
                "text": _display_value(comment.session_id)
            },
            "project_id": {
                "text": _display_value(project_name)
            },
            "agent_id": {
                "text": _display_value(agent_name)
            },
            "author": {
                "text": _display_value(comment.author)
            },
            "source_id": {
                "text": _display_value(comment.source_id)
            },
            "issue_number": {
                "text": _display_value(comment.issue_number)
            },
            "public": {
                "text": "yes" if comment.public else "no"
            },
            "approved": {
                "text": "yes" if comment.approved else "no"
            },
            "sent": {
                "text": "yes" if comment.sent else "no"
            },
            "sent_at": {
                "text": _format_timestamp(comment.sent_at)
            },
            "created_at": {
                "text": _format_timestamp(comment.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(comment.updated_at)
            },
        }
        rows.append({"id": comment.id, "cells": cells})
    return rows


def _build_remote_token_rows(tokens: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for token in tokens:
        cells = {
            "id": {
                "text": _display_value(token.id)
            },
            "provider": {
                "text": _display_value(token.provider)
            },
            "note": {
                "text": _display_value(token.note),
                "href": f"/ui/remote-tokens/{token.id}/edit"
            },
            "token": {
                "text": _mask_token(token.token)
            },
            "user_id": {
                "text": _display_value(token.user_id)
            },
            "user_login": {
                "text": _display_value(token.user_login)
            },
            "created_at": {
                "text": _format_timestamp(token.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(token.updated_at)
            },
        }
        rows.append({"id": token.id, "cells": cells})
    return rows


def _build_issue_source_rows(
    sources: list[Any],
    project_lookup: dict[str, str],
    token_lookup: dict[str, str],
    agent_lookup: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in sources:
        project_name = project_lookup.get(source.project_id, source.project_id) if source.project_id else None
        token_name = token_lookup.get(source.token_id, source.token_id) if source.token_id else None
        agent_name = agent_lookup.get(source.agent_id, source.agent_id) if source.agent_id else None
        cells = {
            "id": {
                "text": _display_value(source.id)
            },
            "provider": {
                "text": _display_value(source.provider)
            },
            "project_id": {
                "text": _display_value(project_name)
            },
            "repo": {
                "text": _display_value(source.repo),
                "href": f"/ui/issue-sources/{source.id}/edit"
            },
            "token_id": {
                "text": _display_value(token_name)
            },
            "agent_id": {
                "text": _display_value(agent_name)
            },
            "state": {
                "text": _display_value(source.state)
            },
            "labels": {
                "text": ", ".join(source.labels) if source.labels else "n/a"
            },
            "enabled": {
                "text": "yes" if source.enabled else "no"
            },
            "auto_start": {
                "text": "yes" if source.auto_start else "no"
            },
            "poll_interval_seconds": {
                "text": f"{source.poll_interval_seconds}s"
            },
            "created_at": {
                "text": _format_timestamp(source.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(source.updated_at)
            },
        }
        rows.append({"id": source.id, "cells": cells})
    return rows


def _build_agent_response_rows(responses: list[Any], agent_lookup: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for response in responses:
        agent_name = agent_lookup.get(response.agent_id, response.agent_id) if response.agent_id else None
        cells = {
            "id": {
                "text": _display_value(response.id)
            },
            "agent_id": {
                "text": _display_value(agent_name)
            },
            "pattern": {
                "text": _display_value(response.pattern),
                "href": f"/ui/agent-responses/{response.id}/edit"
            },
            "response": {
                "text": _truncate_text(response.response, 80)
            },
            "created_at": {
                "text": _format_timestamp(response.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(response.updated_at)
            },
        }
        rows.append({"id": response.id, "cells": cells})
    return rows


def _build_repo_resource_rows(resources: list[Any], project_lookup: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for resource in resources:
        project_name = project_lookup.get(resource.project_id, resource.project_id) if resource.project_id else None
        cells = {
            "id": {
                "text": _display_value(resource.id)
            },
            "path": {
                "text": _display_value(resource.path),
                "href": f"/ui/repo-resources/{resource.id}/edit"
            },
            "project_id": {
                "text": _display_value(project_name)
            },
            "repo_mode": {
                "text": _display_value(resource.repo_mode)
            },
            "status": {
                "text": _display_value(resource.status)
            },
            "session_id": {
                "text": _display_value(resource.session_id)
            },
            "agent_id": {
                "text": _display_value(resource.agent_id)
            },
            "last_used_at": {
                "text": _format_timestamp(resource.last_used_at)
            },
            "created_at": {
                "text": _format_timestamp(resource.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(resource.updated_at)
            },
        }
        rows.append({"id": resource.id, "cells": cells})
    return rows


def _build_work_item_rows(work_items: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in work_items:
        cells = {
            "work_id": {
                "text": _display_value(item.work_id),
                "href": f"/ui/work-items/{item.work_id}"
            },
            "source_id": {
                "text": _display_value(item.source_id)
            },
            "priority": {
                "text": _display_value(item.priority)
            },
            "status": {
                "text": _display_value(item.status)
            },
            "attempts": {
                "text": _display_value(item.attempts)
            },
            "run_after": {
                "text": _format_timestamp(item.run_after)
            },
            "last_error": {
                "text": _truncate_text(item.last_error, 80)
            },
            "created_at": {
                "text": _format_timestamp(item.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(item.updated_at)
            },
        }
        rows.append({"id": item.work_id, "cells": cells})
    return rows


def _build_session_rows(sessions: list[Any], project_lookup: dict[str, str], agent_lookup: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session in sessions:
        project_name = project_lookup.get(session.project_id, session.project_id) if session.project_id else None
        agent_name = agent_lookup.get(session.agent_id, session.agent_id) if session.agent_id else None
        cells = {
            "id": {
                "text": _display_value(session.id),
                "href": f"/ui/sessions/{session.id}"
            },
            "project_id": {
                "text": _display_value(project_name)
            },
            "agent_id": {
                "text": _display_value(agent_name)
            },
            "ticket_id": {
                "text": _display_value(session.ticket_id)
            },
            "status": {
                "text": _display_value(session.status)
            },
            "repo_path": {
                "text": _display_value(session.repo_path)
            },
            "thread_ts": {
                "text": _display_value(session.thread_ts)
            },
            "mcp_conversation_id": {
                "text": _display_value(session.mcp_conversation_id)
            },
            "claude_session_id": {
                "text": _display_value(session.claude_session_id)
            },
            "last_output_at": {
                "text": _format_timestamp(session.last_output_at)
            },
            "created_at": {
                "text": _format_timestamp(session.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(session.updated_at)
            },
        }
        rows.append({"id": session.id, "cells": cells})
    return rows


def _build_metric_definition_rows(definitions: list[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for defn in definitions:
        cells = {
            "id": {
                "text": _display_value(defn.id),
                "href": f"/ui/metric-definitions/{defn.id}/edit"
            },
            "metric_type": {
                "text": _display_value(defn.metric_type),
                "href": f"/ui/metric-definitions/{defn.id}/edit"
            },
            "recording_frequency_minutes": {
                "text": _display_value(defn.recording_frequency_minutes)
            },
            "enabled": {
                "text": "Yes" if defn.enabled else "No"
            },
            "created_at": {
                "text": _format_timestamp(defn.created_at)
            },
            "updated_at": {
                "text": _format_timestamp(defn.updated_at)
            },
        }
        rows.append({"id": defn.id, "cells": cells})
    return rows


def _build_agent_metrics_log_rows(logs: list[Any], agent_lookup: dict[str, str], definition_lookup: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log in logs:
        agent_name = agent_lookup.get(log.agent_id, log.agent_id) if log.agent_id else None
        metric_type = definition_lookup.get(log.metric_definition_id, log.metric_definition_id)
        cells = {
            "id": {
                "text": _display_value(log.id)
            },
            "agent_id": {
                "text": _display_value(agent_name)
            },
            "metric_type": {
                "text": _display_value(metric_type)
            },
            "value": {
                "text": _display_value(log.value)
            },
            "recorded_at": {
                "text": _format_timestamp(log.recorded_at)
            },
            "created_at": {
                "text": _format_timestamp(log.created_at)
            },
        }
        rows.append({"id": log.id, "cells": cells})
    return rows


def _build_table_context(
    *,
    database: Database,
    request: Request,
    user: str,
    model: str,
    title: str,
    description: Optional[str],
    create_label: Optional[str],
    create_url: Optional[str],
    rows: list[dict[str, Any]],
    empty_message: str,
) -> dict[str, Any]:
    config = LIST_TABLE_CONFIGS.get(model)
    if not config:
        raise HTTPException(status_code=400, detail="Unknown table model")
    columns = config["columns"]
    available_keys = [column["key"] for column in columns]
    selected = _resolve_table_columns(database, user, model, available_keys, config["default"])
    bulk_actions = config.get("bulk_actions", [])
    return {
        "table_model": model,
        "table_title": title,
        "table_description": description,
        "table_columns": columns,
        "table_columns_meta": {
            column["key"]: column
            for column in columns
        },
        "table_columns_lookup": {
            column["key"]: column["label"]
            for column in columns
        },
        "table_selected_columns": selected,
        "table_rows": rows,
        "table_create_label": create_label,
        "table_create_url": create_url,
        "table_empty_message": empty_message,
        "table_return_to": _safe_return_to(request, f"/ui/{model}"),
        "table_search_action": request.url.path,
        "table_search_query": request.query_params.get("q", "").strip(),
        "table_search_placeholder": "Search",
        "table_bulk_actions": bulk_actions,
    }


async def _fetch_github_user(token: str) -> tuple[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "wintermute",
    }
    async with aiohttp.ClientSession() as session:
        async with session.get("https://api.github.com/user", headers=headers) as response:
            payload = await response.json()
            if response.status >= 400:
                message = payload.get("message", "GitHub API error")
                raise HTTPException(status_code=400, detail=f"GitHub token validation failed: {message}")
            user_id = str(payload.get("id", ""))
            login = str(payload.get("login", ""))
            return user_id, login


async def _fetch_gitlab_user(token: str, base_url: Optional[str] = None) -> tuple[str, str]:
    headers = {
        "Accept": "application/json",
        "PRIVATE-TOKEN": token,
        "User-Agent": "wintermute",
    }
    if base_url:
        api_base = base_url.rstrip("/")
        if not api_base.endswith("/api/v4"):
            api_base = f"{api_base}/api/v4"
    else:
        api_base = os.environ.get("WINTERMUTE_GITLAB_API_BASE", "https://gitlab.com/api/v4").rstrip("/")
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{api_base}/user", headers=headers) as response:
            payload = await response.json()
            if response.status >= 400:
                message = payload.get("message", "GitLab API error") if isinstance(payload, dict) else "GitLab API error"
                raise HTTPException(status_code=400, detail=f"GitLab token validation failed: {response.status} {message}")
            user_id = str(payload.get("id", ""))
            login = str(payload.get("username", ""))
            return user_id, login


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


def _generate_unique_string(base: str, existing: set[str]) -> str:
    """Generate a unique string by appending a number suffix if needed.

    If base is 'bob' and 'bob' exists, returns 'bob2'.
    If 'bob2' also exists, returns 'bob3', etc.
    """
    if base not in existing:
        return base

    # Strip any existing numeric suffix to get the base
    match = re.match(r"^(.+?)(\d+)$", base)
    if match:
        stem = match.group(1)
        start = int(match.group(2)) + 1
    else:
        stem = base
        start = 2

    counter = start
    while True:
        candidate = f"{stem}{counter}"
        if candidate not in existing:
            return candidate
        counter += 1


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
    lines.extend([
        "",
        f"Repo path: {repo_path}",
        f"Branch: {branch_name}",
        "",
        "Please do the work, commit your changes, and push the branch for review.",
        "If you need clarification, ask your questions clearly.",
    ])
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


def _slack_client(database: Database) -> WebClient:
    bot = database.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
    if not bot:
        raise HTTPException(status_code=400, detail="Slack bot token not configured")
    return WebClient(token=bot.reference)


def _slack_admin_user_id(database: Database) -> Optional[str]:
    record = database.get_credential_by_name(SLACK_PROVIDER, "admin_user_id")
    if not record:
        return None
    return record.reference


def _update_slack_channel_filter(database: Database) -> None:
    channels = []
    for project in database.list_projects():
        if project.slack_channel_id:
            channels.append(project.slack_channel_id)
    source = database.get_task_source(SlackSource.id)
    if source:
        config = dict(source.config)
        config["channels"] = channels
        database.upsert_task_source(
            SlackSource.id,
            source.enabled,
            source.base_priority,
            source.poll_interval_seconds,
            config,
        )


def _find_channel_id(client: WebClient, channel_name: str) -> Optional[str]:
    cursor = None
    while True:
        resp = client.conversations_list(cursor=cursor, limit=200)
        for channel in resp.get("channels", []):
            if channel.get("name") == channel_name:
                return channel.get("id")
        cursor = resp.get("response_metadata", {}).get("next_cursor") or None
        if not cursor:
            break
    return None


def _create_agent_slack_channel(database: Database, agent: Any, channel_name: str) -> tuple[Optional[str], Optional[str]]:
    """Create a Slack channel for an agent and return (channel_id, error_message).

    The Slack channel name follows the pattern: {agent_slug}-{vm_name}
    If the channel already exists, returns the existing channel's ID.
    Returns (channel_id, None) on success, (None, error_message) on failure.
    """
    logger = logging.getLogger(__name__)
    slack_bot = database.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
    if not slack_bot:
        error = "Slack bot token not configured. Add it in Credentials → Slack."
        logger.warning("Slack bot token not configured - cannot create channel")
        return None, error

    # Build Slack channel name: {agent_name}-{vm_name}
    vm = database.get_vm_target(agent.vm_target_id) if agent.vm_target_id else None
    if vm:
        slack_channel_name = f"{agent.name}-{vm.name}".lower().replace(" ", "-")
    else:
        slack_channel_name = f"{agent.name}".lower().replace(" ", "-")

    # Slack channel names must be lowercase, no spaces, max 80 chars, alphanumeric + hyphens only
    slack_channel_name = re.sub(r"[^a-z0-9-]", "-", slack_channel_name)
    slack_channel_name = re.sub(r"-+", "-", slack_channel_name).strip("-")[:80]

    client = WebClient(token=slack_bot.reference)
    channel_id = None
    error_msg = None

    try:
        resp = client.conversations_create(name=slack_channel_name, is_private=False)
        channel_id = resp.get("channel", {}).get("id")
        logger.info("Created Slack channel %s (ID: %s) for agent %s", slack_channel_name, channel_id, agent.slug)
    except Exception as exc:
        error_text = str(exc)
        if "name_taken" in error_text:
            channel_id = _find_channel_id(client, slack_channel_name)
            if channel_id:
                logger.info("Slack channel %s already exists (ID: %s)", slack_channel_name, channel_id)
            else:
                error_msg = f"Channel '{slack_channel_name}' exists but bot can't access it. Invite the bot to the channel."
                logger.warning(error_msg)
        elif "missing_scope" in error_text:
            error_msg = "Slack bot missing 'channels:write' scope. Update bot permissions in Slack."
            logger.warning("Slack channel create missing scope for agent %s", agent.slug)
        elif "invalid_name" in error_text:
            error_msg = f"Invalid Slack channel name: {slack_channel_name}"
            logger.warning(error_msg)
        else:
            error_msg = f"Slack API error: {exc}"
            logger.warning("Slack channel create failed for agent %s: %s", agent.slug, exc)

    # Bot joins channel and invites admin user
    if channel_id:
        # Ensure bot is in the channel
        try:
            client.conversations_join(channel=channel_id)
            logger.info("Bot joined channel %s", slack_channel_name)
        except Exception as exc:
            if "already_in_channel" not in str(exc):
                logger.warning("Failed to join channel %s: %s", slack_channel_name, exc)

        # Invite admin user if configured
        admin_user_id = _slack_admin_user_id(database)
        if admin_user_id:
            try:
                client.conversations_invite(channel=channel_id, users=admin_user_id)
                logger.info("Invited admin user %s to channel %s", admin_user_id, slack_channel_name)
            except Exception as exc:
                if "already_in_channel" not in str(exc):
                    logger.warning("Failed to invite admin to channel %s: %s", slack_channel_name, exc)

    return channel_id, error_msg


def _parse_integrity_error(exc: IntegrityError) -> tuple[Optional[str], str]:
    """Parse an IntegrityError to extract field name and user-friendly message.

    Returns (field_name, message). field_name may be None if we can't determine it.
    """
    error_str = str(exc.orig) if exc.orig else str(exc)

    # SQLite: "UNIQUE constraint failed: table.column"
    if "UNIQUE constraint failed:" in error_str:
        match = re.search(r"UNIQUE constraint failed: (\w+)\.(\w+)", error_str)
        if match:
            table, column = match.groups()
            return column, f"A record with this {column} already exists. Please choose a different value."

    # Generic fallback
    if "UNIQUE" in error_str.upper():
        return None, "This value is already in use. Please choose a different value."

    return None, f"Database constraint violation: {error_str}"


def _growl_message(saved: Optional[str]) -> Optional[str]:
    messages = {
        "slack": "Saved Slack token data",
        "github": "Saved GitHub token data",
        "project_created": "Project created",
        "project_updated": "Project updated",
        "project_deleted": "Deletion of project successful",
        "project_source_created": "Project source created",
        "ticket_created": "Ticket created",
        "ticket_updated": "Ticket updated",
        "ticket_deleted": "Ticket deleted",
        "vm_created": "VM target created",
        "vm_updated": "VM target updated",
        "vm_deleted": "VM target deleted",
        "agent_created": "Agent created",
        "agent_updated": "Agent updated",
        "agent_deleted": "Agent deleted",
        "mapping_created": "Project mapping created",
        "mapping_updated": "Project mapping updated",
        "mapping_deleted": "Project mapping deleted",
        "github_source": "Saved GitHub source settings",
        "gitlab_source": "Saved GitLab source settings",
        "slack_source": "Saved Slack source settings",
        "ticket_auto_start_source": "Saved ticket auto-start settings",
        "standup_source": "Saved standup settings",
        "github_source_created": "GitHub source created",
        "github_source_updated": "GitHub source updated",
        "github_source_deleted": "GitHub source deleted",
        "gitlab_source_created": "GitLab source created",
        "gitlab_source_updated": "GitLab source updated",
        "gitlab_source_deleted": "GitLab source deleted",
        "github_token_created": "GitHub token created",
        "github_token_updated": "GitHub token updated",
        "github_token_deleted": "GitHub token deleted",
        "gitlab_token_created": "GitLab token created",
        "gitlab_token_updated": "GitLab token updated",
        "gitlab_token_deleted": "GitLab token deleted",
        "api_token_created": "API token created",
        "api_token_updated": "API token updated",
        "api_token_deleted": "API token deleted",
        "session_updated": "Session updated",
        "session_deleted": "Session deleted",
        "comment_updated": "Comment updated",
        "comment_deleted": "Comment deleted",
        "agent_response_created": "Agent response created",
        "agent_response_updated": "Agent response updated",
        "agent_response_deleted": "Agent response deleted",
        "columns_updated": "Column selection saved",
    }
    if not saved:
        return None
    # Handle dynamic clone messages like "cloned_3"
    if saved.startswith("cloned_"):
        try:
            count = int(saved.split("_")[1])
            return f"Cloned {count} item{'s' if count != 1 else ''}"
        except (IndexError, ValueError):
            return "Items cloned"
    return messages.get(saved)


def _parse_permissions(form: Any) -> dict[str, dict[str, bool]]:
    permissions: dict[str, dict[str, bool]] = {}
    for model in API_PERMISSION_MODELS:
        key = model["key"]
        actions: dict[str, bool] = {}
        for action in API_PERMISSION_ACTIONS:
            field = f"perm-{key}-{action}"
            actions[action] = form.get(field) == "on"
        permissions[key] = actions
    return permissions


def create_app(db: Optional[Database] = None) -> FastAPI:
    database = db or Database()
    database.initialize()
    if not database.get_task_source(SlackSource.id):
        database.upsert_task_source(
            SlackSource.id,
            SlackSource.enabled,
            SlackSource.base_priority,
            SlackSource.poll_interval_seconds,
            config={"channels": []},
        )
    else:
        _update_slack_channel_filter(database)
    if not database.get_task_source(CommentDispatchSource.id):
        database.upsert_task_source(
            CommentDispatchSource.id,
            CommentDispatchSource.enabled,
            CommentDispatchSource.base_priority,
            CommentDispatchSource.poll_interval_seconds,
            config={},
        )
    app = FastAPI(title="Wintermute Admin")
    base_dir = os.path.dirname(__file__)
    repo_root = os.path.abspath(os.path.join(base_dir, os.pardir, os.pardir))
    static_css_path = os.path.join(base_dir, "static", "style.css")
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))

    class NoCacheStaticFiles(StaticFiles):

        async def get_response(self, path: str, scope: Any) -> Response:
            response = await super().get_response(path, scope)
            response.headers.update({
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            })
            return response

    app.mount("/static", NoCacheStaticFiles(directory=os.path.join(base_dir, "static")), name="static")
    secret_key = (os.environ.get("WINTERMUTE_WEB_SECRET") or secrets.token_urlsafe(32))
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

    def _render_markdown(text: Optional[str]) -> str:
        raw = text or ""
        try:
            import markdown as md
            import bleach
        except Exception:
            escaped = html.escape(raw)
            return escaped.replace("\n", "<br />")
        rendered = md.markdown(raw, extensions=["extra", "sane_lists", "nl2br"])
        allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + [
            "p",
            "pre",
            "code",
            "blockquote",
            "ul",
            "ol",
            "li",
            "strong",
            "em",
            "hr",
            "br",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        ]
        allowed_attrs = {
            "a": ["href", "title", "rel", "target"],
            "code": ["class"],
            "span": ["class"],
        }
        cleaned = bleach.clean(rendered, tags=allowed_tags, attributes=allowed_attrs, strip=True)
        return bleach.linkify(cleaned)

    def _render_comment_body(text: Optional[str]) -> str:
        raw = text or ""
        raw = re.sub(r"\r", "", raw)
        raw = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", raw)
        raw = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", raw)
        raw = re.sub(
            r"\x1b\[[0-9;?]*[A-Za-z]",
            lambda match: match.group(0) if match.group(0).endswith("m") else "",
            raw,
        )
        raw = re.sub(r"\[[0-9][0-9;?<>]*[A-Za-z]", "", raw)
        raw = re.sub(r"\[[0-9][0-9;?<>]*[\\\"]", "", raw)
        raw = re.sub(r"\][0-9][0-9;?<>]*[\\\"]", "", raw)
        raw = re.sub(r"\[(?:;)*[A-Za-z]", "", raw)
        raw = re.sub(r"\[(?:;)*[\\\"]", "", raw)
        raw = re.sub(r"\](?:;)*[\\\"]", "", raw)
        raw = re.sub(r"(?m)^M{3,}$", "", raw)
        raw = re.sub(r"M{5,}", "", raw)
        try:
            import bleach
            from ansi2html import Ansi2HTMLConverter
        except Exception:
            return f"<pre>{html.escape(raw)}</pre>"
        ansi_re = re.compile(r"\x1b\[[0-9;]*m")
        if ansi_re.search(raw):
            converter = Ansi2HTMLConverter(inline=True)
            rendered = converter.convert(raw, full=False)
            rendered = f"<pre>{rendered}</pre>"
        else:
            raw = re.sub(r"\[[0-9;]*m", "", raw)
            rendered = f"<pre>{html.escape(raw)}</pre>"
        allowed_tags = list(bleach.sanitizer.ALLOWED_TAGS) + ["span", "pre", "code", "br"]
        allowed_attrs = {"span": ["style", "class"], "pre": ["class"], "code": ["class"]}
        return bleach.clean(rendered, tags=allowed_tags, attributes=allowed_attrs, strip=True)

    def _comment_author(record: Any) -> str:
        if getattr(record, "author", None):
            return str(record.author)
        if record.agent_id:
            agent = database.get_agent(record.agent_id)
            if agent:
                return agent.name
            return f"agent:{record.agent_id}"
        if record.session_id:
            return "agent"
        return "user"

    def _render_template(request: Request, template_name: str, context: dict[str, Any]) -> Response:
        try:
            static_version = int(os.path.getmtime(static_css_path))
        except OSError:
            static_version = 0
        # Auto-inject user from session if not already in context
        user = context.get("user") or request.session.get("user")
        response = templates.TemplateResponse(
            template_name,
            {
                "request": request,
                "static_version": static_version,
                "user": user,
                **context
            },
        )
        response.headers.update({
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        })
        return response

    def _ensure_agent_response(agent_id: str, pattern: str, response: str) -> None:
        existing = database.list_agent_responses(agent_id=agent_id)
        for item in existing:
            if item.pattern.strip() == pattern.strip():
                if item.response.strip() != response.strip():
                    database.update_agent_response(item.id, response=response, pattern=pattern)
                return
        database.insert_agent_response(str(uuid.uuid4()), agent_id, pattern, response)

    def _write_env_token(env_path: str, token_value: str) -> None:
        if not env_path:
            return
        path = env_path
        if not os.path.isabs(path):
            path = os.path.abspath(os.path.join(repo_root, env_path))
        if not path.startswith(repo_root):
            raise HTTPException(status_code=400, detail="env_path must be inside the repo")
        key = "WINTERMUTE_ADMIN_API_TOKEN"
        lines: list[str] = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        updated = False
        for idx, line in enumerate(lines):
            if line.startswith(f"{key}="):
                lines[idx] = f"{key}={token_value}"
                updated = True
                break
        if not updated:
            lines.append(f"{key}={token_value}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")

    def _record_to_dict(record: Any) -> dict[str, Any]:
        if hasattr(record, "__dataclass_fields__"):
            data = asdict(record)
            if isinstance(data.get("checkpoint"), (dict, list)):
                data["checkpoint"] = data["checkpoint"]
            return data
        return dict(record)

    def _comment_to_dict(record: Any) -> dict[str, Any]:
        data = _record_to_dict(record)
        return {
            "id": data.get("id"),
            "ticket_id": data.get("ticket_id"),
            "session_id": data.get("session_id"),
            "project_id": data.get("project_id"),
            "agent_id": data.get("agent_id"),
            "source_id": data.get("source_id"),
            "issue_number": data.get("issue_number"),
            "body": data.get("body"),
            "author": _comment_author(record),
            "rendered_body": _render_comment_body(data.get("body")),
            "public": data.get("public"),
            "approved": data.get("approved"),
            "sent": data.get("sent"),
            "sent_at": data.get("sent_at"),
            "origin": getattr(record, "origin", None),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
        }

    async def _run_comment_websocket(
        websocket: WebSocket,
        get_comments: callable,
        get_session: callable,
        comment_to_dict: callable = _comment_to_dict,
    ) -> None:
        """Shared WebSocket handler for comment streams.

        Args:
            websocket: The WebSocket connection
            get_comments: Function(since: str) -> list of comments
            get_session: Function() -> session record or None
            comment_to_dict: Function(comment) -> dict for JSON serialization
        """
        await websocket.accept()
        session_data = getattr(websocket, "session", {})
        if not session_data or not session_data.get("user"):
            await websocket.close(code=1008)
            return

        def _recent_activity(iso_value: Optional[str], seconds: int = 45) -> bool:
            if not iso_value:
                return False
            try:
                ts = datetime.fromisoformat(iso_value)
            except ValueError:
                return False
            now = datetime.now(timezone.utc)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return (now - ts).total_seconds() <= seconds

        last_seen = websocket.query_params.get("since")
        try:
            while True:
                rows = get_comments(last_seen)
                if rows:
                    last_seen = rows[-1].created_at
                    for row in rows:
                        await websocket.send_json({"type": "comment", "data": comment_to_dict(row)})
                session = get_session()
                if session and session.status == "running":
                    # Use most recent timestamp - prompt_sent_at for awaiting, last_output_at for active
                    last_activity = max(session.prompt_sent_at or "", session.last_output_at or "") or None
                    active = (bool(session.awaiting_response) and bool(session.last_user_message) and _recent_activity(last_activity))
                    await websocket.send_json({"type": "typing", "data": {"active": active}})
                else:
                    await websocket.send_json({"type": "typing", "data": {"active": False}})
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
        except WebSocketDisconnect:
            return

    def _normalize_permissions(payload: Any) -> dict[str, dict[str, bool]]:
        permissions: dict[str, dict[str, bool]] = {}
        if not isinstance(payload, dict):
            return _parse_permissions({})
        for model in API_PERMISSION_MODELS:
            key = model["key"]
            actions: dict[str, bool] = {}
            raw_actions = payload.get(key, {})
            for action in API_PERMISSION_ACTIONS:
                actions[action] = bool(raw_actions.get(action))
            permissions[key] = actions
        return permissions

    def _get_bearer_token(request: Request) -> Optional[str]:
        header = request.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header.split(" ", 1)[1].strip()
        return request.headers.get("X-API-Token")

    def _read_pid_info(pid_file: str, started_file: str) -> dict[str, Optional[str]]:
        pid = None
        started_at = None
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as handle:
                    value = handle.read().strip()
                    pid = value or None
            except OSError:
                pid = None
        if os.path.exists(started_file):
            try:
                with open(started_file, "r", encoding="utf-8") as handle:
                    value = handle.read().strip()
                    started_at = value or None
            except OSError:
                started_at = None
        return {"pid": pid, "started_at": started_at}

    def _restart_script(script_name: str, pid_file: str, process_match: str) -> dict[str, Any]:
        logger = logging.getLogger(__name__)
        logger.info("Restart requested for %s", script_name)
        killed: list[int] = []
        pid = None
        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as handle:
                    pid = int(handle.read().strip() or "0")
            except ValueError:
                pid = None

        def _kill_pid(target_pid: int) -> None:
            try:
                os.kill(target_pid, signal.SIGTERM)
                killed.append(target_pid)
            except Exception as exc:
                logger.warning("Failed to stop pid %s for %s: %s", target_pid, script_name, exc)

        if pid:
            _kill_pid(pid)

        try:
            proc = subprocess.run(
                ["pgrep", "-f", process_match],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for line in proc.stdout.splitlines():
                line = line.strip()
                if line.isdigit():
                    _kill_pid(int(line))
        except Exception as exc:
            logger.warning("Failed to scan processes for %s: %s", script_name, exc)

        return {"ok": True, "message": f"restart requested for {script_name}", "killed": killed}

    def _require_api_permission(request: Request, model: str, action: str):
        token_value = _get_bearer_token(request)
        if not token_value:
            raise HTTPException(status_code=401, detail="Missing API token")
        token_record = database.get_api_token_by_value(token_value)
        if not token_record:
            raise HTTPException(status_code=401, detail="Invalid API token")
        permissions = token_record.permissions.get(model, {})
        if not permissions.get(action):
            raise HTTPException(status_code=403, detail="Permission denied")
        return token_record

    def _require_login_or_api(request: Request, model: str, action: str) -> tuple[Optional[str], Optional[Any]]:
        user = request.session.get("user")
        if user:
            return user, None
        token_record = _require_api_permission(request, model, action)
        return None, token_record

    @app.get("/status")
    def status(user: str = Depends(_require_login)) -> dict[str, Any]:
        row = database.get_supervisor_state()
        if not row:
            return {"status": "unknown", "message": "Supervisor has not reported yet"}
        return {
            "status": row.status,
            "current_work_id": row.current_work_id,
            "last_action": row.last_action,
            "queue_depth": row.queue_depth,
            "updated_at": row.updated_at,
        }

    @app.get("/sources")
    def list_sources(user: str = Depends(_require_login)) -> list[dict[str, Any]]:
        rows = database.list_task_sources()
        result = []
        for row in rows:
            result.append({
                "id": row.id,
                "enabled": bool(row.enabled),
                "base_priority": row.base_priority,
                "poll_interval_seconds": row.poll_interval_seconds,
                "config": row.config,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
            })
        return result

    @app.put("/sources/{source_id}")
    def update_source(source_id: str, payload: TaskSourceUpdate, user: str = Depends(_require_login)) -> dict[str, Any]:
        rows = {row.id: row for row in database.list_task_sources()}
        row = rows.get(source_id)
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")
        database.upsert_task_source(
            source_id,
            payload.enabled if payload.enabled is not None else bool(row.enabled),
            payload.base_priority if payload.base_priority is not None else row.base_priority,
            payload.poll_interval_seconds if payload.poll_interval_seconds is not None else row.poll_interval_seconds,
            payload.config if payload.config is not None else row.config,
        )
        return {"status": "ok"}

    @app.post("/sources/{source_id}/ui_update")
    async def update_source_ui(source_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        row = database.get_task_source(source_id)
        if not row:
            if source_id == GitHubIssuesSource.id:
                database.upsert_task_source(
                    GitHubIssuesSource.id,
                    GitHubIssuesSource.enabled,
                    GitHubIssuesSource.base_priority,
                    GitHubIssuesSource.poll_interval_seconds,
                    config={},
                )
                row = database.get_task_source(source_id)
            elif source_id == GitLabIssuesSource.id:
                database.upsert_task_source(
                    GitLabIssuesSource.id,
                    GitLabIssuesSource.enabled,
                    GitLabIssuesSource.base_priority,
                    GitLabIssuesSource.poll_interval_seconds,
                    config={},
                )
                row = database.get_task_source(source_id)
            elif source_id == TicketAutoStartSource.id:
                database.upsert_task_source(
                    TicketAutoStartSource.id,
                    TicketAutoStartSource.enabled,
                    TicketAutoStartSource.base_priority,
                    TicketAutoStartSource.poll_interval_seconds,
                    config={},
                )
                row = database.get_task_source(source_id)
            elif source_id == StandupSource.id:
                database.upsert_task_source(
                    StandupSource.id,
                    StandupSource.enabled,
                    StandupSource.base_priority,
                    StandupSource.poll_interval_seconds,
                    config={},
                )
                row = database.get_task_source(source_id)
            if not row:
                raise HTTPException(status_code=404, detail="Source not found")
        enabled = form.get("enabled") == "on"
        poll_interval = form.get("poll_interval_seconds")
        config = dict(row.config or {})
        if source_id == SlackSource.id:
            _update_slack_channel_filter(database)
            config = database.get_task_source(SlackSource.id).config
        elif source_id == GitHubIssuesSource.id:
            config = row.config
        elif source_id == GitLabIssuesSource.id:
            config = row.config
        elif source_id == TicketAutoStartSource.id:
            config = row.config
        elif source_id == StandupSource.id:
            time_raw = str(form.get("standup_time", "")).strip()
            timezone_raw = str(form.get("standup_timezone", "")).strip()
            channel_raw = str(form.get("standup_channel", "")).strip()
            if time_raw:
                config["time"] = time_raw
            if timezone_raw:
                config["timezone"] = timezone_raw
            if channel_raw:
                config["channel"] = channel_raw
            else:
                config.pop("channel", None)
        else:
            channels_raw = str(form.get("channels", ""))
            config["channels"] = _parse_channels(channels_raw)
        poll_interval_seconds = row.poll_interval_seconds
        if poll_interval:
            poll_interval_seconds = int(poll_interval)
        database.upsert_task_source(
            source_id,
            enabled,
            row.base_priority,
            poll_interval_seconds,
            config,
        )
        if source_id == GitHubIssuesSource.id:
            saved = "github_source"
        elif source_id == GitLabIssuesSource.id:
            saved = "gitlab_source"
        elif source_id == TicketAutoStartSource.id:
            saved = "ticket_auto_start_source"
        elif source_id == StandupSource.id:
            return RedirectResponse("/ui/standup?saved=standup_source", status_code=303)
        else:
            saved = "slack_source"
        return RedirectResponse(f"/ui?saved={saved}", status_code=303)

    @app.get("/work-items")
    def list_work_items(status: Optional[str] = None, user: str = Depends(_require_login)) -> list[dict[str, Any]]:
        rows = database.list_work_items(status=status)
        return [{
            "work_id": row.work_id,
            "source_id": row.source_id,
            "priority": row.priority,
            "status": row.status,
            "checkpoint": row.checkpoint,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "run_after": row.run_after,
            "attempts": row.attempts,
            "last_error": row.last_error,
        } for row in rows]

    @app.get("/work-items/{work_id}")
    def get_work_item(work_id: str, user: str = Depends(_require_login)) -> dict[str, Any]:
        record = database.get_work_item(work_id)
        if not record:
            raise HTTPException(status_code=404, detail="Work item not found")
        return {
            "work_id": record.work_id,
            "source_id": record.source_id,
            "priority": record.priority,
            "status": record.status,
            "checkpoint": record.checkpoint,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "run_after": record.run_after,
            "attempts": record.attempts,
            "last_error": record.last_error,
        }

    @app.post("/work-items/{work_id}/requeue")
    def requeue_work_item(work_id: str, user: str = Depends(_require_login)) -> dict[str, Any]:
        record = database.get_work_item(work_id)
        if not record:
            raise HTTPException(status_code=404, detail="Work item not found")
        database.update_work_item_status(work_id, "queued", run_after=utc_now())
        return {"status": "queued"}

    @app.post("/work-items/{work_id}/cancel")
    def cancel_work_item(work_id: str, user: str = Depends(_require_login)) -> dict[str, Any]:
        record = database.get_work_item(work_id)
        if not record:
            raise HTTPException(status_code=404, detail="Work item not found")
        database.update_work_item_status(work_id, "failed", last_error="cancelled")
        return {"status": "cancelled"}

    @app.get("/credentials")
    def list_credentials(user: str = Depends(_require_login)) -> list[dict[str, Any]]:
        return [{
            "id": row.id,
            "name": row.name,
            "provider": row.provider,
            "reference": row.reference,
            "note": row.note,
            "created_at": row.created_at,
        } for row in database.list_credentials()]

    @app.post("/credentials")
    def create_credential(payload: CredentialCreate, user: str = Depends(_require_login)) -> dict[str, Any]:
        cred_id = str(uuid.uuid4())
        database.insert_credential(
            cred_id,
            payload.name,
            payload.provider,
            payload.reference,
            payload.note,
        )
        return {"id": cred_id}

    @app.post("/slack/credentials")
    async def set_slack_credentials(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        bot_token = str(form.get("bot_token", "")).strip()
        app_token = str(form.get("app_token", "")).strip()
        admin_user_id = str(form.get("admin_user_id", "")).strip()
        if bot_token:
            database.upsert_credential(
                cred_id=f"{SLACK_PROVIDER}:{SLACK_BOT_TOKEN_NAME}",
                name=SLACK_BOT_TOKEN_NAME,
                provider=SLACK_PROVIDER,
                reference=bot_token,
            )
        if app_token:
            database.upsert_credential(
                cred_id=f"{SLACK_PROVIDER}:{SLACK_APP_TOKEN_NAME}",
                name=SLACK_APP_TOKEN_NAME,
                provider=SLACK_PROVIDER,
                reference=app_token,
            )
        if admin_user_id:
            database.upsert_credential(
                cred_id=f"{SLACK_PROVIDER}:admin_user_id",
                name="admin_user_id",
                provider=SLACK_PROVIDER,
                reference=admin_user_id,
            )
        return RedirectResponse("/ui?saved=slack", status_code=303)

    @app.post("/github-tokens")
    async def create_github_token(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        token = str(form.get("token", "")).strip()
        note = str(form.get("note", "")).strip() or None
        if not token:
            raise HTTPException(status_code=400, detail="GitHub token is required")
        user_id, login = await _fetch_github_user(token)
        database.insert_github_token(
            token_id=str(uuid.uuid4()),
            token=token,
            note=note,
            user_id=user_id,
            user_login=login,
        )
        return_to = str(form.get("return_to", "/ui/github-tokens")).strip() or "/ui/github-tokens"
        if not return_to.startswith("/ui"):
            return_to = "/ui/github-tokens"
        return RedirectResponse(f"{return_to}?saved=github_token_created", status_code=303)

    @app.post("/github-tokens/{token_id}/edit")
    async def update_github_token(token_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        token = str(form.get("token", "")).strip()
        note = str(form.get("note", "")).strip() or None
        existing = database.get_github_token(token_id)
        if not existing:
            raise HTTPException(status_code=404, detail="GitHub token not found")
        user_id = existing.user_id
        user_login = existing.user_login
        if token:
            user_id, user_login = await _fetch_github_user(token)
        database.update_github_token(
            token_id,
            token=token or existing.token,
            note=note if note is not None else existing.note,
            user_id=user_id,
            user_login=user_login,
        )
        return RedirectResponse("/ui/github-tokens?saved=github_token_updated", status_code=303)

    @app.post("/github-tokens/{token_id}/delete")
    async def delete_github_token(token_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_github_token(token_id)
        return RedirectResponse("/ui/github-tokens?saved=github_token_deleted", status_code=303)

    @app.post("/gitlab-tokens")
    async def create_gitlab_token(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        token = str(form.get("token", "")).strip()
        note = str(form.get("note", "")).strip() or None
        if not token:
            raise HTTPException(status_code=400, detail="GitLab token is required")
        user_id, login = await _fetch_gitlab_user(token)
        database.insert_gitlab_token(
            token_id=str(uuid.uuid4()),
            token=token,
            note=note,
            user_id=user_id,
            user_login=login,
        )
        return_to = str(form.get("return_to", "/ui/gitlab-tokens")).strip() or "/ui/gitlab-tokens"
        if not return_to.startswith("/ui"):
            return_to = "/ui/gitlab-tokens"
        return RedirectResponse(f"{return_to}?saved=gitlab_token_created", status_code=303)

    @app.post("/gitlab-tokens/{token_id}/edit")
    async def update_gitlab_token(token_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        token = str(form.get("token", "")).strip()
        note = str(form.get("note", "")).strip() or None
        existing = database.get_gitlab_token(token_id)
        if not existing:
            raise HTTPException(status_code=404, detail="GitLab token not found")
        user_id = existing.user_id
        user_login = existing.user_login
        if token:
            user_id, user_login = await _fetch_gitlab_user(token)
        database.update_gitlab_token(
            token_id,
            token=token or existing.token,
            note=note if note is not None else existing.note,
            user_id=user_id,
            user_login=user_login,
        )
        return RedirectResponse("/ui/gitlab-tokens?saved=gitlab_token_updated", status_code=303)

    @app.post("/gitlab-tokens/{token_id}/delete")
    async def delete_gitlab_token(token_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_gitlab_token(token_id)
        return RedirectResponse("/ui/gitlab-tokens?saved=gitlab_token_deleted", status_code=303)

    # --- Unified Remote Token POST routes ---

    @app.post("/remote-tokens")
    async def create_remote_token(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        provider = str(form.get("provider", "github")).strip()
        token = str(form.get("token", "")).strip()
        note = str(form.get("note", "")).strip() or None
        base_url = str(form.get("base_url", "")).strip() or None
        if not token:
            raise HTTPException(status_code=400, detail="Token is required")
        if provider == "github":
            user_id, login = await _fetch_github_user(token)
        elif provider == "gitlab":
            user_id, login = await _fetch_gitlab_user(token, base_url)
        else:
            raise HTTPException(status_code=400, detail="Invalid provider")
        database.insert_remote_token(
            token_id=str(uuid.uuid4()),
            provider=provider,
            token=token,
            note=note,
            base_url=base_url,
            user_id=user_id,
            user_login=login,
        )
        return_to = str(form.get("return_to", "/ui/remote-tokens")).strip() or "/ui/remote-tokens"
        if not return_to.startswith("/ui"):
            return_to = "/ui/remote-tokens"
        return RedirectResponse(f"{return_to}?saved=remote_token_created", status_code=303)

    @app.post("/remote-tokens/{token_id}/edit")
    async def update_remote_token(token_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        provider = str(form.get("provider", "")).strip()
        token = str(form.get("token", "")).strip()
        note = str(form.get("note", "")).strip() or None
        base_url = str(form.get("base_url", "")).strip() or None
        existing = database.get_remote_token(token_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Remote token not found")
        user_id = existing.user_id
        user_login = existing.user_login
        effective_provider = provider or existing.provider
        effective_base_url = base_url if base_url is not None else existing.base_url
        if token:
            if effective_provider == "github":
                user_id, user_login = await _fetch_github_user(token)
            elif effective_provider == "gitlab":
                user_id, user_login = await _fetch_gitlab_user(token, effective_base_url)
        database.update_remote_token(
            token_id,
            provider=provider or None,
            token=token or None,
            note=note,
            base_url=base_url,
            user_id=user_id,
            user_login=user_login,
        )
        return RedirectResponse("/ui/remote-tokens?saved=remote_token_updated", status_code=303)

    @app.post("/remote-tokens/{token_id}/delete")
    async def delete_remote_token(token_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_remote_token(token_id)
        return RedirectResponse("/ui/remote-tokens?saved=remote_token_deleted", status_code=303)

    @app.get("/ui/api-tokens")
    def api_tokens_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        tokens = database.list_api_tokens()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="api_tokens",
            title="API Tokens",
            description="Manage issued API credentials.",
            create_label="Add API Token",
            create_url="/ui/api-tokens/create?return_to=/ui/api-tokens",
            rows=_build_api_token_rows(tokens),
            empty_message="No API tokens yet.",
        )
        return _render_template(
            request,
            "api_tokens.html",
            {
                "title": "API Tokens",
                "active_nav": "api_tokens",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/api-tokens/create")
    def api_tokens_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/api-tokens")
        if not return_to.startswith("/"):
            return_to = "/ui/api-tokens"
        return _render_template(
            request,
            "api_token_create.html",
            {
                "title": "Create API Token",
                "active_nav": "api_tokens",
                "growl_message": None,
                "return_to": return_to,
                "permission_models": API_PERMISSION_MODELS,
                "permission_actions": API_PERMISSION_ACTIONS,
            },
        )

    @app.get("/ui/api-tokens/{token_id}/edit")
    def api_tokens_edit_ui(token_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        token = database.get_api_token(token_id)
        if not token:
            raise HTTPException(status_code=404, detail="API token not found")
        token_display = f"{token.token[:4]}...{token.token[-4:]}" if len(token.token) > 8 else token.token
        show_token = request.query_params.get("show_token") == "1"
        return _render_template(
            request,
            "api_token_edit.html",
            {
                "title": "Edit API Token",
                "active_nav": "api_tokens",
                "growl_message": None,
                "token": token,
                "token_display": token_display,
                "show_token": show_token,
                "permissions": token.permissions,
                "permission_models": API_PERMISSION_MODELS,
                "permission_actions": API_PERMISSION_ACTIONS,
            },
        )

    @app.post("/api-tokens")
    async def create_api_token(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        token_value = str(form.get("token", "")).strip()
        env_path = str(form.get("env_path", "")).strip()
        permissions = _parse_permissions(form)
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        if not token_value:
            token_value = secrets.token_urlsafe(32)
        token_id = str(uuid.uuid4())
        database.insert_api_token(
            token_id=token_id,
            name=name,
            token=token_value,
            permissions=permissions,
        )
        if env_path:
            _write_env_token(env_path, token_value)
        return_to = str(form.get("return_to", "/ui/api-tokens")).strip() or "/ui/api-tokens"
        if not return_to.startswith("/"):
            return_to = "/ui/api-tokens"
        if not env_path:
            return_to = f"/ui/api-tokens/{token_id}/edit?show_token=1"
        separator = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{return_to}{separator}saved=api_token_created", status_code=303)

    @app.post("/api-tokens/{token_id}/edit")
    async def update_api_token(token_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        token_value = str(form.get("token", "")).strip()
        permissions = _parse_permissions(form)
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        database.update_api_token(
            token_id,
            name=name,
            token=token_value or None,
            permissions=permissions,
        )
        return RedirectResponse("/ui/api-tokens?saved=api_token_updated", status_code=303)

    @app.post("/api-tokens/{token_id}/delete")
    async def delete_api_token(token_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_api_token(token_id)
        return RedirectResponse("/ui/api-tokens?saved=api_token_deleted", status_code=303)

    def _hash_plain_password(password: str) -> tuple[str, str]:
        salt = secrets.token_bytes(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        return _hash_password(password, salt), salt_b64

    def _require_fields(payload: dict[str, Any], fields: list[str]) -> None:
        missing = [field for field in fields if not payload.get(field)]
        if missing:
            raise HTTPException(status_code=400, detail=f"Missing fields: {', '.join(missing)}")

    def _unsupported_api_action(model: str) -> None:
        raise HTTPException(status_code=405, detail=f"{model} is read-only")

    def _api_model_handlers() -> dict[str, dict[str, Any]]:
        return {
            "projects": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_projects()],
                "get":
                database.get_project,
                "required": ["name"],
                "create":
                lambda payload: database.insert_project(
                    str(uuid.uuid4()),
                    payload["name"],
                    payload.get("slug") or _slugify(payload["name"]),
                    payload.get("slack_channel_id"),
                    payload.get("prompt_template"),
                    payload.get("max_repo_resources", 3),
                    repo_mode=payload.get("repo_mode"),
                    repo_path=payload.get("repo_path"),
                    repo_url=payload.get("repo_url"),
                ),
                "update":
                lambda item_id, payload: database.update_project(
                    item_id,
                    name=payload.get("name"),
                    slug=payload.get("slug"),
                    slack_channel_id=payload.get("slack_channel_id"),
                    prompt_template=payload.get("prompt_template"),
                    max_repo_resources=payload.get("max_repo_resources"),
                    repo_mode=payload.get("repo_mode"),
                    repo_path=payload.get("repo_path"),
                    repo_url=payload.get("repo_url"),
                ),
                "delete":
                database.delete_project,
            },
            "comments": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_comments()],
                "get":
                database.get_comment,
                "required": ["ticket_id", "body"],
                "create":
                lambda payload: database.insert_comment(
                    payload.get("id") or str(uuid.uuid4()),
                    payload["ticket_id"],
                    payload.get("session_id"),
                    payload.get("project_id"),
                    payload.get("agent_id"),
                    payload.get("author"),
                    payload.get("source_id"),
                    payload.get("issue_number"),
                    payload["body"],
                    bool(payload.get("public")),
                    bool(payload.get("approved")),
                ),
                "update":
                lambda item_id, payload: database.update_comment(
                    item_id,
                    body=payload.get("body"),
                    public=payload.get("public"),
                    approved=payload.get("approved"),
                    sent=payload.get("sent"),
                    sent_at=payload.get("sent_at"),
                ),
                "delete":
                database.delete_comment,
            },
            "repo_resources": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_repo_resources()],
                "get":
                database.get_repo_resource,
                "required": ["project_id", "repo_mode", "path", "status"],
                "create":
                lambda payload: database.insert_repo_resource(
                    payload.get("id") or str(uuid.uuid4()),
                    payload["project_id"],
                    payload["repo_mode"],
                    payload["path"],
                    payload["status"],
                    session_id=payload.get("session_id"),
                    agent_id=payload.get("agent_id"),
                ),
                "update":
                lambda item_id, payload: database.update_repo_resource(
                    item_id,
                    status=payload.get("status"),
                    session_id=payload.get("session_id"),
                    agent_id=payload.get("agent_id"),
                    last_used_at=payload.get("last_used_at"),
                ),
                "delete":
                database.delete_repo_resource,
            },
            "tickets": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_tickets()],
                "get":
                database.get_ticket,
                "required": ["project_id", "title"],
                "create":
                lambda payload: database.insert_ticket(
                    payload.get("id") or str(uuid.uuid4()),
                    payload["project_id"],
                    payload["title"],
                    payload.get("description"),
                    agent_id=payload.get("agent_id"),
                    assigned_to=payload.get("assigned_to"),
                    estimate=payload.get("estimate"),
                    status=payload.get("status") or "open",
                    internal_notes=payload.get("internal_notes"),
                    source_url=payload.get("source_url"),
                    auto_start=bool(payload.get("auto_start", False)),
                ),
                "update":
                lambda item_id, payload: database.update_ticket(
                    item_id,
                    agent_id=("" if "agent_id" in payload and payload.get("agent_id") is None else payload.get("agent_id")) if "agent_id" in payload else None,
                    title=payload.get("title"),
                    description=payload.get("description"),
                    internal_notes=payload.get("internal_notes"),
                    assigned_to=payload.get("assigned_to"),
                    estimate=payload.get("estimate"),
                    status=payload.get("status"),
                    source_url=payload.get("source_url"),
                    auto_start=payload.get("auto_start"),
                ),
                "delete":
                database.delete_ticket,
            },
            "vms": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_vm_targets()],
                "get":
                database.get_vm_target,
                "required": ["name", "host"],
                "create":
                lambda payload: database.insert_vm_target(
                    str(uuid.uuid4()),
                    payload["name"],
                    payload["host"],
                    payload.get("user") or "root",
                    int(payload.get("port") or 22),
                ),
                "update":
                lambda item_id, payload: database.update_vm_target(
                    item_id,
                    name=payload.get("name"),
                    host=payload.get("host"),
                    user=payload.get("user"),
                    port=int(payload["port"]) if payload.get("port") is not None else None,
                ),
                "delete":
                database.delete_vm_target,
            },
            "agents": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_agents()],
                "get":
                database.get_agent,
                "required": ["name", "command"],
                "create":
                lambda payload: database.insert_agent(
                    str(uuid.uuid4()),
                    payload["name"],
                    payload.get("slug") or _slugify(payload["name"]),
                    payload["command"],
                    payload.get("session_mode") or "tmux",
                    payload.get("required_ssh_options"),
                    payload.get("env_vars"),
                    payload.get("mcp_config"),
                    payload.get("trust_level"),
                    payload.get("input_echo_prefix"),
                    payload.get("response_prefix"),
                ),
                "update":
                lambda item_id, payload: database.update_agent(
                    item_id,
                    name=payload.get("name"),
                    slug=payload.get("slug"),
                    command=payload.get("command"),
                    session_mode=payload.get("session_mode"),
                    required_ssh_options=payload.get("required_ssh_options"),
                    env_vars=payload.get("env_vars"),
                    mcp_config=payload.get("mcp_config"),
                    trust_level=payload.get("trust_level"),
                    input_echo_prefix=payload.get("input_echo_prefix"),
                    response_prefix=payload.get("response_prefix"),
                ),
                "delete":
                database.delete_agent,
            },
            "agent_responses": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_agent_responses()],
                "get":
                database.get_agent_response,
                "required": ["agent_id", "pattern", "response"],
                "create":
                lambda payload: database.insert_agent_response(
                    payload.get("id") or str(uuid.uuid4()),
                    payload["agent_id"],
                    payload["pattern"],
                    payload["response"],
                ),
                "update":
                lambda item_id, payload: database.update_agent_response(
                    item_id,
                    agent_id=payload.get("agent_id"),
                    pattern=payload.get("pattern"),
                    response=payload.get("response"),
                ),
                "delete":
                database.delete_agent_response,
            },
            "sessions": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_sessions()],
                "get":
                database.get_session,
                "required": ["project_id", "agent_id", "repo_path"],
                "create":
                lambda payload: database.insert_session(
                    payload.get("id") or str(uuid.uuid4()),
                    payload["project_id"],
                    payload["agent_id"],
                    payload.get("ticket_id"),
                    payload.get("status") or "running",
                    payload["repo_path"],
                    payload.get("thread_ts"),
                    payload.get("mcp_conversation_id"),
                ),
                "update":
                lambda item_id, payload: database.update_session(
                    item_id,
                    status=payload.get("status"),
                    thread_ts=payload.get("thread_ts"),
                    mcp_conversation_id=payload.get("mcp_conversation_id"),
                    last_output=payload.get("last_output"),
                    last_output_offset=payload.get("last_output_offset"),
                    output_buffer=payload.get("output_buffer"),
                    output_buffer_updated_at=payload.get("output_buffer_updated_at"),
                    prompt_pending=payload.get("prompt_pending"),
                    prompt_sent_at=payload.get("prompt_sent_at"),
                    last_output_at=payload.get("last_output_at"),
                ),
                "delete":
                database.delete_session,
            },
            "supervisor_state": {
                "list": lambda: [state] if (state := database.get_supervisor_state()) else [],
                "get": lambda item_id: database.get_supervisor_state(),
                "required": [],
                "create": lambda payload: _unsupported_api_action("supervisor_state"),
                "update": lambda item_id, payload: _unsupported_api_action("supervisor_state"),
                "delete": lambda item_id: _unsupported_api_action("supervisor_state"),
            },
            "github_tokens": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_github_tokens()],
                "get":
                database.get_github_token,
                "required": ["token"],
                "create":
                lambda payload: database.insert_github_token(
                    str(uuid.uuid4()),
                    payload.get("note"),
                    payload["token"],
                    payload.get("user_id"),
                    payload.get("user_login"),
                ),
                "update":
                lambda item_id, payload: database.update_github_token(
                    item_id,
                    token=payload.get("token"),
                    note=payload.get("note"),
                    user_id=payload.get("user_id"),
                    user_login=payload.get("user_login"),
                ),
                "delete":
                database.delete_github_token,
            },
            "github_sources": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_github_sources()],
                "get":
                database.get_github_source,
                "required": ["project_id", "owner", "repo"],
                "create":
                lambda payload: database.insert_github_source(
                    payload.get("id") or str(uuid.uuid4()),
                    payload.get("token_id"),
                    payload.get("agent_id"),
                    payload["project_id"],
                    payload["owner"],
                    payload["repo"],
                    payload.get("state") or "open",
                    payload.get("labels") or [],
                    bool(payload.get("enabled", True)),
                    bool(payload.get("auto_start", False)),
                ),
                "update":
                lambda item_id, payload: database.update_github_source(
                    item_id,
                    token_id=payload.get("token_id"),
                    agent_id=payload.get("agent_id"),
                    project_id=payload.get("project_id"),
                    owner=payload.get("owner"),
                    repo=payload.get("repo"),
                    state=payload.get("state"),
                    labels=payload.get("labels"),
                    enabled=payload.get("enabled"),
                    auto_start=payload.get("auto_start"),
                ),
                "delete":
                database.delete_github_source,
            },
            "gitlab_tokens": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_gitlab_tokens()],
                "get":
                database.get_gitlab_token,
                "required": ["token"],
                "create":
                lambda payload: database.insert_gitlab_token(
                    str(uuid.uuid4()),
                    payload.get("note"),
                    payload["token"],
                    payload.get("user_id"),
                    payload.get("user_login"),
                ),
                "update":
                lambda item_id, payload: database.update_gitlab_token(
                    item_id,
                    token=payload.get("token"),
                    note=payload.get("note"),
                    user_id=payload.get("user_id"),
                    user_login=payload.get("user_login"),
                ),
                "delete":
                database.delete_gitlab_token,
            },
            "gitlab_sources": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_gitlab_sources()],
                "get":
                database.get_gitlab_source,
                "required": ["project_id", "project_path"],
                "create":
                lambda payload: database.insert_gitlab_source(
                    payload.get("id") or str(uuid.uuid4()),
                    payload.get("token_id"),
                    payload.get("agent_id"),
                    payload["project_id"],
                    payload["project_path"],
                    payload.get("state") or "open",
                    payload.get("labels") or [],
                    bool(payload.get("enabled", True)),
                    bool(payload.get("auto_start", False)),
                ),
                "update":
                lambda item_id, payload: database.update_gitlab_source(
                    item_id,
                    token_id=payload.get("token_id"),
                    agent_id=payload.get("agent_id"),
                    project_id=payload.get("project_id"),
                    project_path=payload.get("project_path"),
                    state=payload.get("state"),
                    labels=payload.get("labels"),
                    enabled=payload.get("enabled"),
                    auto_start=payload.get("auto_start"),
                ),
                "delete":
                database.delete_gitlab_source,
            },
            "task_sources": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_task_sources()],
                "get":
                database.get_task_source,
                "required": ["id"],
                "create":
                lambda payload: database.upsert_task_source(
                    payload["id"],
                    bool(payload.get("enabled", False)),
                    int(payload.get("base_priority") or 50),
                    int(payload.get("poll_interval_seconds") or 60),
                    payload.get("config") or {},
                ),
                "update":
                lambda item_id, payload: database.upsert_task_source(
                    item_id,
                    bool(payload.get("enabled", False)),
                    int(payload.get("base_priority") or 50),
                    int(payload.get("poll_interval_seconds") or 60),
                    payload.get("config") or {},
                ),
                "delete":
                database.delete_task_source,
            },
            "work_items": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_work_items()],
                "get":
                database.get_work_item,
                "required": ["work_id", "source_id"],
                "create":
                lambda payload: database.insert_work_item_if_absent(
                    payload["work_id"],
                    payload["source_id"],
                    int(payload.get("priority") or 50),
                    payload.get("checkpoint") or {},
                    payload.get("status") or "queued",
                ),
                "update":
                lambda item_id, payload: database.update_work_item_status(
                    item_id,
                    payload.get("status") or "queued",
                    checkpoint=payload["checkpoint"] if "checkpoint" in payload else None,
                    priority=payload.get("priority"),
                    run_after=payload.get("run_after"),
                    attempts=payload.get("attempts"),
                    last_error=payload.get("last_error"),
                    last_traceback=payload.get("last_traceback"),
                    clear_errors=(("last_error" in payload and payload.get("last_error") is None) or
                                  ("last_traceback" in payload and payload.get("last_traceback") is None)),
                ),
                "delete":
                database.delete_work_item,
            },
            "credentials": {
                "list":
                lambda: [_record_to_dict(row) for row in database.list_credentials()],
                "get":
                database.get_credential,
                "required": ["name", "provider", "reference"],
                "create":
                lambda payload: database.insert_credential(
                    str(uuid.uuid4()),
                    payload["name"],
                    payload["provider"],
                    payload["reference"],
                    payload.get("note"),
                ),
                "update":
                lambda item_id, payload: database.update_credential(
                    item_id,
                    name=payload.get("name"),
                    provider=payload.get("provider"),
                    reference=payload.get("reference"),
                    note=payload.get("note"),
                ),
                "delete":
                database.delete_credential,
            },
            "users": {
                "list": lambda: [_record_to_dict(row) for row in database.list_users()],
                "get": database.get_user_by_id,
                "required": ["username", "password"],
                "create": lambda payload: _create_api_user(payload),
                "update": lambda item_id, payload: _update_api_user(item_id, payload),
                "delete": database.delete_user,
            },
        }

    def _create_api_user(payload: dict[str, Any]) -> None:
        _require_fields(payload, ["username", "password"])
        password_hash, salt = _hash_plain_password(payload["password"])
        database.insert_user(str(uuid.uuid4()), payload["username"], password_hash, salt)

    def _update_api_user(user_id: str, payload: dict[str, Any]) -> None:
        password_hash = None
        salt = None
        if payload.get("password"):
            password_hash, salt = _hash_plain_password(payload["password"])
        database.update_user(
            user_id,
            username=payload.get("username"),
            password_hash=password_hash,
            salt=salt,
        )

    @app.get("/api/{model}")
    async def api_list(model: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "read")
        rows = handlers["list"]()
        fields_raw = request.query_params.get("fields")
        if model == "comments" and request.query_params:
            filters = dict(request.query_params)
            if "fields" in filters:
                filters.pop("fields", None)

            def matches(row: dict[str, Any]) -> bool:
                for key, expected in filters.items():
                    if key not in row:
                        return False
                    value = row.get(key)
                    if value is None:
                        return False
                    if str(value) != expected:
                        return False
                return True

            rows = [row for row in rows if matches(row)]
        if fields_raw:
            fields = [item.strip() for item in fields_raw.split(",") if item.strip()]
            if not fields:
                return {"data": rows}
            allowed = set(rows[0].keys()) if rows else set()
            unknown = [field for field in fields if field not in allowed]
            if unknown:
                raise HTTPException(status_code=400, detail=f"Unknown fields: {', '.join(unknown)}")
            rows = [{field: row.get(field) for field in fields} for row in rows]
        return {"data": rows}

    @app.post("/api/{model}")
    async def api_create(model: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "create")
        payload = await request.json()
        _require_fields(payload, handlers.get("required", []))
        handlers["create"](payload)
        return {"ok": True}

    @app.delete("/api/{model}")
    async def api_delete_filtered(model: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "delete")
        if model != "comments":
            raise HTTPException(status_code=405, detail=f"{model} does not support bulk delete")
        filters = dict(request.query_params)
        if not filters:
            raise HTTPException(status_code=400, detail="At least one filter is required")
        fields_raw = filters.pop("fields", None)
        if fields_raw is not None:
            filters.pop("fields", None)

        def matches(row: dict[str, Any]) -> bool:
            for key, expected in filters.items():
                if key not in row:
                    return False
                value = row.get(key)
                if value is None:
                    return False
                if str(value) != expected:
                    return False
            return True

        rows = [row for row in handlers["list"]() if matches(row)]
        for row in rows:
            database.delete_comment(row["id"])
        return {"ok": True, "deleted": len(rows)}

    @app.post("/api/admin/restart-web")
    async def api_restart_web(request: Request) -> dict[str, Any]:
        _require_api_permission(request, "admin", "update")
        pid_file = os.environ.get("WINTERMUTE_WEB_PID_FILE", os.path.join(repo_root, ".runtime", "web.pid"))
        return _restart_script("run_web.sh", pid_file, "uvicorn wintermute.web.app:create_app")

    @app.post("/api/admin/restart-supervisor")
    async def api_restart_supervisor(request: Request) -> dict[str, Any]:
        _require_api_permission(request, "admin", "update")
        pid_file = os.environ.get("WINTERMUTE_SUPERVISOR_PID_FILE", os.path.join(repo_root, ".runtime", "supervisor.pid"))
        return _restart_script("run_supervisor.sh", pid_file, "python -m wintermute.supervisor")

    @app.post("/api/admin/backfill-ticket-sources")
    async def api_backfill_ticket_sources(request: Request) -> dict[str, Any]:
        _require_api_permission(request, "admin", "update")
        updated = database.backfill_ticket_source_urls()
        return {"ok": True, "updated": updated}

    @app.post("/api/{model}/{item_id}")
    async def api_update(model: str, item_id: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers or "update" not in handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "update")
        payload = await request.json()
        handlers["update"](item_id, payload)
        return {"ok": True}

    @app.get("/api/tickets/{ticket_id}/github-comments")
    async def api_ticket_github_comments(ticket_id: str, request: Request) -> dict[str, Any]:
        _require_api_permission(request, "tickets", "read")
        _provider, source_id, issue_number = parse_issue_ticket(ticket_id)
        if not source_id or issue_number is None:
            raise HTTPException(status_code=400, detail="Ticket is not a GitHub issue")
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        source = database.get_github_source(source_id)
        if not source or not source.token_id:
            raise HTTPException(status_code=400, detail="GitHub source or token missing")
        token_record = database.get_github_token(source.token_id)
        if not token_record:
            raise HTTPException(status_code=400, detail="GitHub token not found")
        force_refresh = request.query_params.get("refresh") in {"1", "true", "yes"}
        comments, error, cached = await _get_github_comments_cached(
            database,
            ticket,
            token_record.token,
            source.owner,
            source.repo,
            issue_number,
            force_refresh=force_refresh,
        )
        payload = {"data": comments, "cached": cached, "fetched_at": ticket.github_comments_fetched_at}
        if error:
            payload["error"] = error
        return payload

    @app.post("/api/tickets/{ticket_id}/comments")
    async def api_ticket_add_comment(ticket_id: str, request: Request) -> dict[str, Any]:
        user, token_record = _require_login_or_api(request, "comments", "create")
        payload = await request.json()
        body = str(payload.get("body") or "").strip()
        if not body:
            raise HTTPException(status_code=400, detail="Comment body required")
        public = bool(payload.get("public", False))
        approved = bool(payload.get("approved", False)) if public else False
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        session = database.get_session_by_ticket(ticket_id)
        source_id, issue_number = _parse_github_ticket(ticket_id)
        comment_id = str(uuid.uuid4())
        database.insert_comment(
            comment_id=comment_id,
            ticket_id=ticket_id,
            session_id=session.id if session else None,
            project_id=ticket.project_id,
            agent_id=session.agent_id if session else None,
            author=user or (token_record.name if token_record else None),
            source_id=source_id,
            issue_number=issue_number,
            body=body,
            public=public,
            approved=approved,
        )
        if session and session.status == "running":
            agent = database.get_agent(session.agent_id)
            message = body
            if agent and agent.response_prefix:
                message = (f"{body}\n\n"
                           f"Please reply with lines starting with '{agent.response_prefix}'.")
            raw_queue = session.queued_user_messages or "[]"
            try:
                queue = json.loads(raw_queue)
                if not isinstance(queue, list):
                    queue = []
            except json.JSONDecodeError:
                queue = []
            queue.append(message)
            database.update_session(
                session.id,
                queued_user_messages=json.dumps(queue),
                awaiting_response=1,
                last_user_message=body,
                prompt_sent_at=utc_now(),
            )
        return {"ok": True, "comment": _comment_to_dict(database.get_comment(comment_id))}

    @app.get("/api/tickets/{ticket_id}/session-status")
    async def api_ticket_session_status(ticket_id: str, request: Request) -> dict[str, Any]:
        _require_login_or_api(request, "sessions", "read")
        session = database.get_session_by_ticket(ticket_id)
        if not session:
            return {"running": False, "session_id": None, "location": None, "repo_path": None}
        if session.status != "running":
            return {"running": False, "session_id": session.id, "location": None, "repo_path": session.repo_path}
        agent = database.get_agent(session.agent_id)
        vm = database.get_vm_target(agent.vm_target_id) if agent and agent.vm_target_id else None
        if not (agent and vm):
            return {
                "running": False,
                "session_id": session.id,
                "location": None,
                "repo_path": session.repo_path,
            }
        if agent.session_mode == "mcp":
            return {
                "running": True,
                "session_id": session.id,
                "location": vm.name,
                "repo_path": session.repo_path,
            }
        spec = build_ssh_spec(vm, agent.required_ssh_options)
        if not is_session_running(spec, session.id):
            database.update_session(session.id, status="done")
            return {
                "running": False,
                "session_id": session.id,
                "location": None,
                "repo_path": session.repo_path,
            }
        return {
            "running": True,
            "session_id": session.id,
            "location": vm.name,
            "repo_path": session.repo_path,
        }

    @app.post("/api/tickets/{ticket_id}/start-session")
    async def api_ticket_start_session(ticket_id: str, request: Request) -> dict[str, Any]:
        user, _token_record = _require_login_or_api(request, "sessions", "create")
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        existing = database.get_session_by_ticket(ticket_id)
        if existing and existing.status == "running":
            return {"ok": True, "message": "Session already running", "session_id": existing.id}
        provider, source_id, issue_number = parse_issue_ticket(ticket_id)
        github_source = database.get_github_source(source_id) if provider == "github" and source_id else None
        gitlab_source = database.get_gitlab_source(source_id) if provider == "gitlab" and source_id else None
        source = github_source or gitlab_source
        if issue_number is not None and provider == "github" and not github_source:
            raise HTTPException(status_code=400, detail="GitHub source not found")
        if issue_number is not None and provider == "gitlab" and not gitlab_source:
            raise HTTPException(status_code=400, detail="GitLab source not found")
        agent_id = ticket.agent_id or (source.agent_id if source else None)
        if not agent_id:
            raise HTTPException(status_code=400, detail="Ticket agent not configured")
        agent = database.get_agent(agent_id)
        if agent:
            approval_pattern = "\n".join([
                r"^You are running Codex in",
                r"^Yes, allow Codex to work in this folder without asking for",
            ])
            _ensure_agent_response(agent_id, approval_pattern, "1")
            command_pattern = "\n".join([
                r"run this command",
                r"yes",
                r"forever",
            ])
            _ensure_agent_response(agent_id, command_pattern, "1")
        if source and ticket.project_id != source.project_id:
            database.update_ticket(ticket_id, project_id=source.project_id)
            ticket = database.get_ticket(ticket_id) or ticket
        project_id = source.project_id if source else ticket.project_id
        project = database.get_project(project_id)
        if not (agent and project):
            raise HTTPException(status_code=400, detail="Project configuration missing")
        if not agent.vm_target_id:
            raise HTTPException(status_code=400, detail="Agent has no VM target configured")
        vm = database.get_vm_target(agent.vm_target_id)
        if not vm:
            raise HTTPException(status_code=400, detail="VM target missing")
        base_options = strip_port_forwards(parse_ssh_options(agent.required_ssh_options))
        if issue_number is not None:
            session_id = f"{project.slug}-{agent.slug}-issue-{issue_number}-{int(time.time())}"
        else:
            short_id = re.sub(r"[^a-zA-Z0-9]+", "-", ticket_id.strip().lower()).strip("-")[:10]
            session_id = f"{project.slug}-{agent.slug}-ticket-{short_id}-{int(time.time())}"
        base_spec = build_ssh_spec_with_options(vm, base_options)
        session_spec = build_ssh_spec(vm, agent.required_ssh_options)
        # Memory check before starting agent
        database.refresh_agent_average_memory_usage(agent.id)
        agent = database.get_agent(agent.id) # Refresh to get updated memory avg
        if agent and vm.required_reserve_memory_gb > 0:
            mem_ok, mem_error = check_vm_memory_available(session_spec, vm, agent)
            if not mem_ok:
                raise HTTPException(status_code=503, detail=mem_error)
        repo_resource, resource_error = database.acquire_repo_resource(
            project=project,
            session_id=session_id,
            agent_id=agent.id,
        )
        if not repo_resource:
            raise HTTPException(status_code=400, detail=resource_error or "Repo resource unavailable")
        try:
            repo_path = ensure_repo(base_spec, project, repo_path=repo_resource.path)
            if not repo_path:
                raise HTTPException(status_code=400, detail="Repository not configured")
            if source and source.token_id and project.repo_url:
                if provider == "github":
                    token_record = database.get_github_token(source.token_id)
                    if token_record:
                        configure_git_push_auth(base_spec, repo_path, project.repo_url, token_record.token)
                elif provider == "gitlab":
                    token_record = database.get_gitlab_token(source.token_id)
                    if token_record:
                        configure_git_push_auth(
                            base_spec,
                            repo_path,
                            project.repo_url,
                            token_record.token,
                            username="oauth2",
                        )
            if is_codex_command(agent.command) and agent.trust_level:
                set_codex_trust(base_spec, repo_path, agent.trust_level)
            repo_mode = project.repo_mode or "mirror"
            if issue_number is not None:
                branch_name = prepare_issue_branch(base_spec, repo_path, int(issue_number))
            elif repo_mode == "local":
                branch_name = prepare_local_ticket_branch(base_spec, repo_path, ticket_id)
            else:
                branch_name = prepare_ticket_branch(base_spec, repo_path, ticket_id)
        except Exception as exc:
            database.release_repo_resource_for_session(session_id)
            raise
        thread_ts = None
        if project.slack_channel_id:
            try:
                client = _slack_client(database)
                if issue_number is not None:
                    text = f"Issue #{issue_number}: {ticket.title}\n{ticket.source_url or ''}\nStarting agent session..."
                else:
                    text = f"Ticket: {ticket.title}\n{ticket.source_url or ''}\nStarting agent session..."
                response = client.chat_postMessage(channel=project.slack_channel_id, text=text)
                thread_ts = response.get("ts")
            except Exception:
                thread_ts = None
        if source and source.agent_id and not ticket.agent_id:
            database.update_ticket(ticket_id, agent_id=source.agent_id)
        database.insert_session(
            session_id=session_id,
            project_id=project.id,
            agent_id=agent.id,
            ticket_id=ticket_id,
            status="running",
            repo_path=repo_path,
            thread_ts=thread_ts,
        )
        if agent.session_mode != "mcp":
            start_session(session_spec, session_id, agent, repo_path)
        if issue_number is not None and source and provider == "github":
            token_record = database.get_github_token(source.token_id) if source.token_id else None
            comments: list[dict[str, Any]] = []
            if token_record:
                comments = await _fetch_issue_comments(token_record.token, source.owner, source.repo, int(issue_number))
            prompt = _issue_prompt(
                {
                    "issue_number": issue_number,
                    "title": ticket.title,
                    "body": ticket.description or "",
                    "html_url": ticket.source_url or "",
                },
                source.owner,
                source.repo,
                comments=comments,
                internal_notes=ticket.internal_notes,
                branch_name=branch_name,
                repo_path=repo_path,
                project_name=project.name,
                project_slug=project.slug,
                prompt_template=project.prompt_template,
            )
        elif issue_number is not None and source and provider == "gitlab":
            token_record = database.get_gitlab_token(source.token_id) if source.token_id else None
            comments = []
            if token_record:
                comments = await _fetch_gitlab_issue_comments(token_record.token, source.project_path, int(issue_number))
            prompt = _gitlab_issue_prompt(
                {
                    "issue_number": issue_number,
                    "title": ticket.title,
                    "body": ticket.description or "",
                    "web_url": ticket.source_url or "",
                },
                source.project_path,
                comments=comments,
                internal_notes=ticket.internal_notes,
                branch_name=branch_name,
                repo_path=repo_path,
                project_name=project.name,
                project_slug=project.slug,
                prompt_template=project.prompt_template,
            )
        else:
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
        session = database.get_session(session_id)
        if session:
            database.update_session(session_id, prompt_pending=prompt)
            database.insert_comment(
                comment_id=str(uuid.uuid4()),
                ticket_id=ticket_id,
                session_id=session_id,
                project_id=project.id,
                agent_id=agent.id,
                author=user or "api",
                source_id=source_id,
                issue_number=issue_number,
                body=prompt,
                public=False,
                approved=True,
            )
        return {
            "ok": True,
            "session_id": session_id,
            "location": vm.name,
            "repo_path": repo_path,
        }

    @app.post("/api/sessions/{session_id}/stop")
    async def api_session_stop(session_id: str, request: Request) -> dict[str, Any]:
        _require_login_or_api(request, "sessions", "update")
        session = database.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        if session.status != "running":
            return {"ok": True, "message": "Session not running"}
        agent = database.get_agent(session.agent_id)
        vm = database.get_vm_target(agent.vm_target_id) if agent and agent.vm_target_id else None
        if not (agent and vm):
            raise HTTPException(status_code=400, detail="Session environment missing")
        if agent.session_mode == "mcp":
            close_mcp_process(session.id)
            database.update_session(
                session.id,
                status="done",
                awaiting_response=0,
                last_user_message="",
                queued_user_messages="[]",
            )
            database.release_repo_resource_for_session(session.id)
            return {"ok": True, "message": "Session stopped"}
        spec = build_ssh_spec(vm, agent.required_ssh_options)
        stop_session(spec, session.id, count=2)
        database.update_session(
            session.id,
            awaiting_response=0,
            last_user_message="",
            queued_user_messages="[]",
        )
        if not is_session_running(spec, session.id):
            database.update_session(session.id, status="done")
            database.release_repo_resource_for_session(session.id)
        return {"ok": True, "message": "Stop signal sent"}

    @app.websocket("/ws/tickets/{ticket_id}")
    async def ws_ticket_comments(websocket: WebSocket, ticket_id: str) -> None:
        await _run_comment_websocket(
            websocket,
            get_comments=lambda since: database.list_comments_since(ticket_id=ticket_id, since=since),
            get_session=lambda: database.get_session_by_ticket(ticket_id),
        )

    @app.get("/api/admin/pids")
    async def api_admin_pids(request: Request) -> dict[str, Any]:
        _require_api_permission(request, "admin", "update")
        web_pid_file = os.environ.get("WINTERMUTE_WEB_PID_FILE", os.path.join(repo_root, ".runtime", "web.pid"))
        web_started_file = os.environ.get("WINTERMUTE_WEB_STARTED_FILE", os.path.join(repo_root, ".runtime", "web.started"))
        supervisor_pid_file = os.environ.get("WINTERMUTE_SUPERVISOR_PID_FILE", os.path.join(repo_root, ".runtime", "supervisor.pid"))
        supervisor_started_file = os.environ.get("WINTERMUTE_SUPERVISOR_STARTED_FILE", os.path.join(repo_root, ".runtime", "supervisor.started"))
        return {
            "web": _read_pid_info(web_pid_file, web_started_file),
            "supervisor": _read_pid_info(supervisor_pid_file, supervisor_started_file),
        }

    @app.get("/api/admin/logs")
    async def api_admin_logs(
        request: Request,
        service: str = Query("web"),
        lines: int = Query(200, ge=1, le=5000),
    ) -> dict[str, Any]:
        _require_api_permission(request, "admin", "update")
        log_dir = os.environ.get("WINTERMUTE_LOG_DIR", os.path.join(repo_root, ".runtime", "logs"))
        default_web = os.path.join(log_dir, "web.log")
        default_supervisor = os.path.join(log_dir, "supervisor.log")
        log_map = {
            "web": os.environ.get("WINTERMUTE_WEB_LOG_FILE", default_web),
            "supervisor": os.environ.get("WINTERMUTE_SUPERVISOR_LOG_FILE", default_supervisor),
        }
        path = log_map.get(service)
        if not path:
            raise HTTPException(status_code=400, detail="Unknown service")
        if not os.path.exists(path):
            return {"service": service, "path": path, "lines": []}
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            data = handle.read().splitlines()
        return {"service": service, "path": path, "lines": data[-lines:]}

    # Sprint ticket management API (must be before catch-all routes)
    @app.get("/api/sprints/{sprint_id}/available-tickets")
    def api_sprint_available_tickets(
        sprint_id: str,
        request: Request,
        user: str = Depends(_require_login),
        q: str = "",
        page: int = 1,
        per_page: int = 20,
    ) -> dict[str, Any]:
        """Get tickets not in this sprint, optionally filtered by search query."""
        tickets = database.list_tickets_not_in_sprint(
            sprint_id,
            status_filter=["open", "in-progress", "needs-feedback"],
        )
        if q:
            q_lower = q.lower()
            tickets = [t for t in tickets if q_lower in t.title.lower()]
        total = len(tickets)
        start = (page - 1) * per_page
        end = start + per_page
        tickets = tickets[start:end]
        return {
            "tickets": [{
                "id": t.id,
                "title": t.title,
                "status": t.status,
                "priority": t.priority,
                "story_points": t.story_points,
            } for t in tickets],
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": (total + per_page - 1) // per_page if total > 0 else 1,
        }

    @app.post("/api/sprints/{sprint_id}/tickets/{ticket_id}")
    def api_add_ticket_to_sprint(
        sprint_id: str,
        ticket_id: str,
        user: str = Depends(_require_login),
    ) -> dict[str, Any]:
        """Add a ticket to a sprint."""
        sprint = database.get_sprint(sprint_id)
        if not sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        added = database.add_ticket_to_sprint(ticket_id, sprint_id)
        return {"ok": True, "added": added, "ticket_id": ticket_id, "sprint_id": sprint_id}

    @app.delete("/api/sprints/{sprint_id}/tickets/{ticket_id}")
    def api_remove_ticket_from_sprint(
        sprint_id: str,
        ticket_id: str,
        user: str = Depends(_require_login),
    ) -> dict[str, Any]:
        """Remove a ticket from a sprint."""
        removed = database.remove_ticket_from_sprint(ticket_id, sprint_id)
        return {"ok": True, "removed": removed, "ticket_id": ticket_id, "sprint_id": sprint_id}

    # -------------------------------------------------------------------------
    # Standalone session API (JSON endpoints for AJAX)
    # -------------------------------------------------------------------------

    @app.get("/api/sessions")
    async def api_list_sessions(
        status: Optional[str] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        user: str = Depends(_require_login),
    ) -> dict:
        """List sessions with optional filters."""
        sessions = database.list_sessions(
            status=status,
            agent_id=agent_id,
            project_id=project_id,
        )
        result = []
        for sess in sessions:
            agent = database.get_agent(sess.agent_id) if sess.agent_id else None
            project = database.get_project(sess.project_id) if sess.project_id else None
            vm = None
            if agent and agent.vm_target_id:
                vm = database.get_vm_target(agent.vm_target_id)
            result.append({
                "id": sess.id,
                "status": sess.status,
                "agent_id": sess.agent_id,
                "agent_name": agent.name if agent else None,
                "agent_slug": agent.slug if agent else None,
                "agent_command": agent.command if agent else None,
                "project_id": sess.project_id,
                "project_name": project.name if project else None,
                "project_slug": project.slug if project else None,
                "vm_target_id": agent.vm_target_id if agent else None,
                "vm_target_name": vm.name if vm else None,
                "ticket_id": sess.ticket_id,
                "workspace_path": sess.workspace_path,
                "created_at": sess.created_at,
            })
        return {"sessions": result}

    @app.get("/api/agents")
    async def api_list_agents(
        command: Optional[str] = None,
        vm_target_id: Optional[str] = None,
        vm_target_name: Optional[str] = None,
        slug: Optional[str] = None,
        name: Optional[str] = None,
        user: str = Depends(_require_login),
    ) -> dict:
        """List agents with optional filters."""
        agents = database.list_agents()
        result = []
        for agent in agents:
            # Apply filters
            if command and command.lower() not in agent.command.lower():
                continue
            if vm_target_id and agent.vm_target_id != vm_target_id:
                continue
            if slug and slug.lower() not in agent.slug.lower():
                continue
            if name and name.lower() not in agent.name.lower():
                continue

            vm = database.get_vm_target(agent.vm_target_id) if agent.vm_target_id else None
            if vm_target_name and (not vm or vm_target_name.lower() not in vm.name.lower()):
                continue

            # Check if running
            running = False
            session_id = None
            all_sessions = database.list_sessions(agent_id=agent.id)
            for sess in all_sessions:
                if not sess.ticket_id and sess.status in ("running", "blocked"):
                    running = True
                    session_id = sess.id
                    break

            result.append({
                "id": agent.id,
                "name": agent.name,
                "slug": agent.slug,
                "command": agent.command,
                "session_mode": agent.session_mode,
                "vm_target_id": agent.vm_target_id,
                "vm_target_name": vm.name if vm else None,
                "session_file_config_id": agent.session_file_config_id,
                "working_directory": agent.working_directory,
                "session_directory": agent.session_directory,
                "initial_prompt": agent.initial_prompt,
                "running": running,
                "session_id": session_id,
            })
        return {"agents": result}

    @app.get("/api/agents/{agent_id}")
    async def api_get_agent(agent_id: str, user: str = Depends(_require_login)) -> dict:
        """Get a single agent by ID."""
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        vm = database.get_vm_target(agent.vm_target_id) if agent.vm_target_id else None

        # Check if running
        running = False
        session_id = None
        all_sessions = database.list_sessions(agent_id=agent.id)
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                running = True
                session_id = sess.id
                break

        return {
            "id": agent.id,
            "name": agent.name,
            "slug": agent.slug,
            "command": agent.command,
            "session_mode": agent.session_mode,
            "vm_target_id": agent.vm_target_id,
            "vm_target_name": vm.name if vm else None,
            "required_ssh_options": agent.required_ssh_options,
            "env_vars": agent.env_vars,
            "mcp_config": agent.mcp_config,
            "trust_level": agent.trust_level,
            "input_echo_prefix": agent.input_echo_prefix,
            "response_prefix": agent.response_prefix,
            "llm_base_url": agent.llm_base_url,
            "llm_api_key": agent.llm_api_key,
            "llm_model": agent.llm_model,
            "session_file_config_id": agent.session_file_config_id,
            "average_memory_usage_mb": agent.average_memory_usage_mb,
            "initial_prompt": agent.initial_prompt,
            "working_directory": agent.working_directory,
            "session_directory": agent.session_directory,
            "running": running,
            "session_id": session_id,
            "created_at": agent.created_at,
            "updated_at": agent.updated_at,
        }

    @app.patch("/api/agents/{agent_id}")
    async def api_update_agent(agent_id: str, request: Request, user: str = Depends(_require_login)) -> dict:
        """Update agent fields via JSON PATCH."""
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        data = await request.json()
        # Build kwargs for update_agent - only include fields that are present
        update_kwargs = {}
        allowed_fields = [
            "name", "slug", "command", "session_mode", "vm_target_id",
            "required_ssh_options", "env_vars", "mcp_config", "trust_level",
            "input_echo_prefix", "response_prefix", "llm_base_url", "llm_api_key",
            "llm_model", "session_file_config_id", "average_memory_usage_mb",
            "initial_prompt", "working_directory", "session_directory",
        ]
        for field in allowed_fields:
            if field in data:
                update_kwargs[field] = data[field]

        if update_kwargs:
            database.update_agent(agent_id, **update_kwargs)

        # Return updated agent
        return await api_get_agent(agent_id, user)

    @app.get("/api/agents/{agent_id}/session-status")
    async def api_agent_session_status(agent_id: str, user: str = Depends(_require_login)) -> dict:
        import subprocess

        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        all_sessions = database.list_sessions(agent_id=agent_id)
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                # Try to get PID from tmux on VM
                # pane_pid is the shell, so we get its child (the actual command)
                pid = None
                if agent.vm_target_id:
                    vm = database.get_vm_target(agent.vm_target_id)
                    if vm:
                        spec = build_ssh_spec(vm, agent.required_ssh_options)
                        session_name = f"wm_{sess.id}"
                        # Get pane_pid (shell), then find its child process (claude)
                        pid_script = (
                            f"pane_pid=$(tmux list-panes -t {session_name} -F '#{{pane_pid}}' 2>/dev/null | head -1); "
                            f"if [ -n \"$pane_pid\" ]; then "
                            f"child=$(pgrep -P $pane_pid 2>/dev/null | head -1); "
                            f"echo \"${{child:-$pane_pid}}\"; fi"
                        )
                        pid_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", pid_script]
                        try:
                            result = subprocess.run(pid_cmd, capture_output=True, text=True, timeout=10)
                            if result.returncode == 0 and result.stdout.strip():
                                pid = result.stdout.strip().split('\n')[0]
                        except Exception:
                            pass
                log_path = f"/tmp/wintermute-{sess.id}.log"
                return {
                    "running": True,
                    "session_id": sess.id,
                    "location": sess.workspace_path or "",
                    "pid": pid,
                    "log_path": log_path,
                }
        return {"running": False, "session_id": None, "location": None, "pid": None, "log_path": None}

    @app.post("/api/agents/{agent_id}/start-session")
    async def api_start_agent_session(
        agent_id: str,
        request: Request,
        user: str = Depends(_require_login),
    ) -> dict:
        import subprocess
        import tempfile
        import shlex
        from datetime import datetime

        # Parse optional JSON body for file_mode
        file_mode = "check"  # default: check timestamps
        try:
            body = await request.json()
            file_mode = body.get("file_mode", "check")
        except Exception:
            pass  # No body or invalid JSON is fine

        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not agent.vm_target_id:
            raise HTTPException(status_code=400, detail="Agent has no VM target configured")
        vm = database.get_vm_target(agent.vm_target_id)
        if not vm:
            raise HTTPException(status_code=400, detail="VM target not found")

        # Check if a standalone session already exists
        all_sessions = database.list_sessions(agent_id=agent_id)
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                raise HTTPException(status_code=409, detail="Session already running")

        # Build SSH spec
        spec = build_ssh_spec(vm, agent.required_ssh_options)

        # Check that required tools are available (tmux for tmux mode, agent command)
        tools_ok, tools_error = ensure_vm_tools(spec, agent.command, agent.session_mode)
        if not tools_ok:
            raise HTTPException(status_code=400, detail=tools_error)

        # Memory check before starting agent
        database.refresh_agent_average_memory_usage(agent.id)
        agent = database.get_agent(agent.id)  # Refresh to get updated memory avg
        if agent and vm.required_reserve_memory_gb > 0:
            mem_ok, mem_error = check_vm_memory_available(spec, vm, agent)
            if not mem_ok:
                raise HTTPException(status_code=503, detail=mem_error)

        # Determine workspace: use working_directory if set, otherwise create temp
        if agent.working_directory:
            # Verify the working directory exists on the VM
            check_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"test -d {shlex.quote(agent.working_directory)} && echo exists"]
            result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
            if result.stdout.strip() != "exists":
                raise HTTPException(status_code=400, detail=f"Working directory does not exist on VM: {agent.working_directory}")
            workspace = agent.working_directory
        else:
            # Create temp workspace on VM target
            mktemp_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"mktemp -d /tmp/agent_{agent.slug}_XXXXXXXX"]
            result = subprocess.run(mktemp_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Failed to create workspace: {result.stderr}")
            workspace = result.stdout.strip()

        # Determine session files directory
        if agent.session_directory:
            if agent.session_directory.startswith("/"):
                # Absolute path
                session_files_dir = agent.session_directory
            else:
                # Relative to workspace
                session_files_dir = f"{workspace}/{agent.session_directory}"
            # Ensure session directory exists
            mkdir_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"mkdir -p {shlex.quote(session_files_dir)}"]
            subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=30)
        else:
            session_files_dir = workspace

        # Check timestamps if file_mode is "check" and session files exist
        if file_mode == "check" and agent.session_file_config_id:
            definitions = database.list_session_file_definitions(agent.session_file_config_id)
            session_files = database.list_session_files(agent_id)
            file_map = {sf.definition_id: sf for sf in session_files}

            # Get timestamps of remote files
            filenames = [defn.filename for defn in definitions]
            if filenames:
                # Build command to get timestamps of all files at once
                stat_parts = " ".join([f"{shlex.quote(session_files_dir)}/{shlex.quote(fn)}" for fn in filenames])
                stat_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}",
                            f"stat -c '%Y %n' {stat_parts} 2>/dev/null || true"]
                stat_result = subprocess.run(stat_cmd, capture_output=True, text=True, timeout=30)

                newer_files = []
                if stat_result.returncode == 0 and stat_result.stdout.strip():
                    for line in stat_result.stdout.strip().split("\n"):
                        if not line.strip():
                            continue
                        parts = line.split(" ", 1)
                        if len(parts) == 2:
                            try:
                                remote_ts = int(parts[0])
                                remote_path = parts[1]
                                remote_filename = os.path.basename(remote_path)
                                # Find corresponding definition and session file
                                for defn in definitions:
                                    if defn.filename == remote_filename:
                                        sf = file_map.get(defn.id)
                                        if sf and sf.updated_at:
                                            # Parse updated_at (ISO format)
                                            local_dt = datetime.fromisoformat(sf.updated_at.replace("Z", "+00:00"))
                                            local_ts = int(local_dt.timestamp())
                                            if remote_ts > local_ts:
                                                newer_files.append({
                                                    "filename": remote_filename,
                                                    "remote_timestamp": remote_ts,
                                                    "local_timestamp": local_ts,
                                                })
                                        break
                            except (ValueError, IndexError):
                                pass

                if newer_files:
                    # Return conflict response - files on target are newer
                    return JSONResponse(
                        status_code=409,
                        content={
                            "conflict": "session_files_newer_on_target",
                            "message": "Session files on target are newer than in Wintermute",
                            "files": newer_files,
                            "workspace": workspace,
                        }
                    )

        # Create the session record
        session_id = str(uuid.uuid4())
        default_initial_prompt = "Read your AGENTS.md file and then wait for further instructions."
        initial_prompt = agent.initial_prompt or default_initial_prompt

        # For non-tmux modes, queue the initial prompt atomically at insert time
        # to avoid race condition with SessionSource polling
        if agent.session_mode != "tmux" and initial_prompt:
            database.insert_session(
                session_id=session_id,
                project_id=None,
                agent_id=agent_id,
                ticket_id=None,
                status="running",
                repo_path=workspace,
                thread_ts=None,
                mcp_conversation_id=None,
                initial_prompt=initial_prompt,
                workspace_path=workspace,
                queued_user_messages=json.dumps([initial_prompt]),
                awaiting_response=1,
                last_user_message=initial_prompt,
                prompt_sent_at=utc_now(),
            )
            # Record initial prompt as comment so it shows in conversation with correct username
            database.insert_comment(
                comment_id=str(uuid.uuid4()),
                ticket_id=None,
                session_id=session_id,
                project_id=None,
                agent_id=agent_id,
                author=user,
                source_id=None,
                issue_number=None,
                body=initial_prompt,
                public=False,
                approved=False,
                agent_session_id=session_id,
                origin="initial_prompt",
            )
        else:
            database.insert_session(
                session_id=session_id,
                project_id=None,
                agent_id=agent_id,
                ticket_id=None,
                status="running",
                repo_path=workspace,
                thread_ts=None,
                mcp_conversation_id=None,
                initial_prompt=initial_prompt,
                workspace_path=workspace,
            )

        # Copy session files to session_files_dir on VM (unless using target files)
        if agent.session_file_config_id and file_mode != "use_target":
            definitions = database.list_session_file_definitions(agent.session_file_config_id)
            session_files = database.list_session_files(agent_id)
            file_map = {sf.definition_id: sf for sf in session_files}
            # Create a local temp dir, write files, then scp them
            with tempfile.TemporaryDirectory() as local_tmp:
                for defn in definitions:
                    content = file_map.get(defn.id, None)
                    if content:
                        file_content = content.content
                    else:
                        file_content = defn.default_content
                    local_path = os.path.join(local_tmp, defn.filename)
                    with open(local_path, "w") as f:
                        f.write(file_content)
                # SCP files to VM (to session_files_dir, not workspace)
                scp_cmd = ["scp", "-P", str(spec.port), *spec.options, "-r", f"{local_tmp}/.", f"{spec.user}@{spec.host}:{session_files_dir}/"]
                scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
                if scp_result.returncode != 0:
                    logging.getLogger(__name__).warning("Failed to copy session files: %s", scp_result.stderr)

        # Start the tmux session (for tmux mode only)
        if agent.session_mode == "tmux":
            start_session(spec, session_id, agent, workspace)

        # Send the initial prompt to tmux agents directly
        initial_prompt_error = None
        if initial_prompt and agent.session_mode == "tmux":
            session_record = database.get_session(session_id)
            if session_record:
                try:
                    send_input(spec, session_record, initial_prompt)
                except Exception as exc:
                    initial_prompt_error = str(exc)
                    logging.getLogger(__name__).warning("Failed to send initial prompt: %s", exc)

        result = {"session_id": session_id, "location": workspace}
        if initial_prompt_error:
            result["warning"] = f"Session started but initial prompt failed: {initial_prompt_error}"
        return result

    @app.post("/api/agents/{agent_id}/session/stop")
    async def api_stop_agent_session(agent_id: str, user: str = Depends(_require_login)) -> dict:
        import subprocess
        import tempfile

        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Find the standalone session
        all_sessions = database.list_sessions(agent_id=agent_id)
        standalone_session = None
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                standalone_session = sess
                break
        if not standalone_session:
            raise HTTPException(status_code=404, detail="No running session")

        # Sync files back from session_files_dir on VM
        if agent.session_file_config_id and standalone_session.workspace_path and agent.vm_target_id:
            vm = database.get_vm_target(agent.vm_target_id)
            if vm:
                spec = build_ssh_spec(vm, agent.required_ssh_options)
                definitions = database.list_session_file_definitions(agent.session_file_config_id)
                # Determine session files directory (same logic as start)
                workspace = standalone_session.workspace_path
                if agent.session_directory:
                    if agent.session_directory.startswith("/"):
                        session_files_dir = agent.session_directory
                    else:
                        session_files_dir = f"{workspace}/{agent.session_directory}"
                else:
                    session_files_dir = workspace
                # Create a local temp dir to receive files
                with tempfile.TemporaryDirectory() as local_tmp:
                    # SCP files from VM (from session_files_dir)
                    scp_cmd = [
                        "scp", "-P",
                        str(spec.port), *spec.options, "-r", f"{spec.user}@{spec.host}:{session_files_dir}/.", f"{local_tmp}/"
                    ]
                    scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
                    if scp_result.returncode == 0:
                        for defn in definitions:
                            if defn.sync_on_exit:
                                local_path = os.path.join(local_tmp, defn.filename)
                                if os.path.exists(local_path):
                                    with open(local_path, "r") as f:
                                        content = f.read()
                                    database.upsert_session_file(agent_id, defn.id, content)
                    else:
                        logging.getLogger(__name__).warning("Failed to sync session files back: %s", scp_result.stderr)

        # Mark session as stopped
        database.update_session(standalone_session.id, status="stopped")
        return {"success": True}

    @app.post("/api/agents/{agent_id}/comments")
    async def api_add_agent_comment(agent_id: str, request: Request, user: str = Depends(_require_login)) -> dict:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Find the standalone session
        all_sessions = database.list_sessions(agent_id=agent_id)
        standalone_session = None
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                standalone_session = sess
                break
        if not standalone_session:
            raise HTTPException(status_code=404, detail="No running session")
        data = await request.json()
        message = str(data.get("body", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="Empty message")
        comment_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        database.insert_comment(
            comment_id=comment_id,
            ticket_id=None,
            session_id=standalone_session.id,
            project_id=None,
            agent_id=agent_id,
            author=user,
            source_id=None,
            issue_number=None,
            body=message,
            public=False,
            approved=False,
            agent_session_id=standalone_session.id,
            origin="web",
        )
        # Queue message for supervisor to dispatch (same as ticket page)
        if standalone_session.status == "running":
            queued_message = message
            if agent.response_prefix:
                queued_message = (f"{message}\n\n"
                                  f"Please reply with lines starting with '{agent.response_prefix}'.")
            raw_queue = standalone_session.queued_user_messages or "[]"
            try:
                queue = json.loads(raw_queue)
                if not isinstance(queue, list):
                    queue = []
            except json.JSONDecodeError:
                queue = []
            queue.append(queued_message)
            database.update_session(
                standalone_session.id,
                queued_user_messages=json.dumps(queue),
                awaiting_response=1,
                last_user_message=message,
                prompt_sent_at=utc_now(),
            )
        # Relay to Slack channels
        try:
            from wintermute.chat.dispatcher import ChatDispatcher
            dispatcher = ChatDispatcher(database)
            import asyncio
            slack_message = f"[{user}] {message}"
            asyncio.create_task(dispatcher.broadcast_to_agent_channels(agent_id, slack_message, platform_filter="slack"))
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to relay to Slack: %s", e)
        return {
            "comment": {
                "id": comment_id,
                "author": user,
                "body": message,
                "origin": "web",
                "created_at": now,
            }
        }

    @app.get("/api/agents/{agent_id}/comments")
    async def api_get_agent_comments(
        agent_id: str,
        since: Optional[str] = None,
        user: str = Depends(_require_login),
    ) -> dict:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Find the active or most recent standalone session
        all_sessions = database.list_sessions(agent_id=agent_id)
        session_id = None
        for sess in all_sessions:
            if not sess.ticket_id:
                if sess.status in ("running", "blocked"):
                    session_id = sess.id
                    break
                elif session_id is None:
                    session_id = sess.id
        if not session_id:
            return {"comments": []}
        comments = database.list_comments_since(agent_session_id=session_id, since=since)
        return {
            "comments": [{
                "id": c.id,
                "author": c.author,
                "body": c.body,
                "origin": c.origin,
                "created_at": c.created_at,
            } for c in comments]
        }

    @app.post("/api/agents/{agent_id}/pull-session-files")
    async def api_pull_session_files(agent_id: str, user: str = Depends(_require_login)) -> dict:
        """Pull session files from the VM and save them to Wintermute."""
        import subprocess
        import tempfile

        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        if not agent.vm_target_id:
            raise HTTPException(status_code=400, detail="Agent has no VM target configured")
        if not agent.session_file_config_id:
            raise HTTPException(status_code=400, detail="Agent has no session file config")

        vm = database.get_vm_target(agent.vm_target_id)
        if not vm:
            raise HTTPException(status_code=400, detail="VM target not found")

        spec = build_ssh_spec(vm, agent.required_ssh_options)

        # Determine working directory and session files directory
        if agent.working_directory:
            workspace = agent.working_directory
        else:
            # No working directory set - check if there's an active session
            all_sessions = database.list_sessions(agent_id=agent_id)
            active_session = None
            for sess in all_sessions:
                if not sess.ticket_id and sess.status in ("running", "blocked"):
                    active_session = sess
                    break
            if active_session and active_session.workspace_path:
                workspace = active_session.workspace_path
            else:
                raise HTTPException(status_code=400, detail="No working directory configured and no active session")

        # Calculate session files directory
        if agent.session_directory:
            if agent.session_directory.startswith("/"):
                session_files_dir = agent.session_directory
            else:
                session_files_dir = f"{workspace}/{agent.session_directory}"
        else:
            session_files_dir = workspace

        definitions = database.list_session_file_definitions(agent.session_file_config_id)
        updated_files = []

        # Create a local temp dir to receive files
        with tempfile.TemporaryDirectory() as local_tmp:
            # SCP files from VM
            scp_cmd = [
                "scp", "-P", str(spec.port), *spec.options, "-r",
                f"{spec.user}@{spec.host}:{session_files_dir}/.", f"{local_tmp}/"
            ]
            scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
            if scp_result.returncode != 0:
                raise HTTPException(status_code=500, detail=f"Failed to pull files from VM: {scp_result.stderr}")

            for defn in definitions:
                local_path = os.path.join(local_tmp, defn.filename)
                if os.path.exists(local_path):
                    with open(local_path, "r") as f:
                        content = f.read()
                    database.upsert_session_file(agent_id, defn.id, content)
                    # Get the updated file record
                    session_files = database.list_session_files(agent_id)
                    for sf in session_files:
                        if sf.definition_id == defn.id:
                            updated_files.append({
                                "id": sf.id,
                                "definition_id": defn.id,
                                "filename": defn.filename,
                                "updated_at": sf.updated_at,
                            })
                            break

        return {"success": True, "files": updated_files}

    @app.websocket("/ws/agents/{agent_id}/comments")
    async def ws_agent_comments(websocket: WebSocket, agent_id: str) -> None:

        def _find_standalone_session():
            """Return the active (running/blocked) standalone session, or most recent."""
            all_sessions = database.list_sessions(agent_id=agent_id)
            for sess in all_sessions:
                if not sess.ticket_id and sess.status in ("running", "blocked"):
                    return sess
            for sess in all_sessions:
                if not sess.ticket_id:
                    return sess
            return None

        def _get_standalone_session_ids():
            """Return all standalone session IDs for this agent."""
            all_sessions = database.list_sessions(agent_id=agent_id)
            return [sess.id for sess in all_sessions if not sess.ticket_id]

        def _get_comments(since):
            session_ids = _get_standalone_session_ids()
            if not session_ids:
                return []
            return database.list_comments_since(agent_session_ids=session_ids, since=since)

        await _run_comment_websocket(
            websocket,
            get_comments=_get_comments,
            get_session=_find_standalone_session,
        )

    @app.post("/api/agents/{agent_id}/channels")
    async def api_create_agent_channel(agent_id: str, request: Request, user: str = Depends(_require_login)) -> dict:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        data = await request.json()
        channel_type = str(data.get("type", "")).strip()
        name = str(data.get("name", "")).strip()
        # Handle null from JSON - data.get returns None, not empty string
        raw_ext_id = data.get("external_channel_id")
        external_channel_id = str(raw_ext_id).strip() if raw_ext_id else None
        if not channel_type or not name:
            raise HTTPException(status_code=400, detail="Missing channel fields")
        # Auto-create Slack channel if type is slack and no external_channel_id provided
        slack_error = None
        if channel_type == "slack" and not external_channel_id:
            external_channel_id, slack_error = _create_agent_slack_channel(database, agent, name)
            if not external_channel_id:
                raise HTTPException(status_code=400, detail=slack_error or "Failed to create Slack channel")
        channel_id = str(uuid.uuid4())
        database.insert_channel(
            channel_id=channel_id,
            agent_id=agent_id,
            channel_type=channel_type,
            name=name,
            external_channel_id=external_channel_id,
            enabled=True,
        )
        return {
            "channel": {
                "id": channel_id,
                "type": channel_type,
                "name": name,
                "external_channel_id": external_channel_id,
                "enabled": True,
            }
        }

    @app.patch("/api/agents/{agent_id}/channels/{channel_id}")
    async def api_update_agent_channel(agent_id: str, channel_id: str, request: Request, user: str = Depends(_require_login)) -> dict:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        data = await request.json()
        updates = {}
        if "name" in data:
            updates["name"] = str(data["name"]).strip() or None
        if "external_channel_id" in data:
            val = str(data["external_channel_id"]).strip()
            updates["external_channel_id"] = val if val and val != "-" else None
        if updates:
            database.update_channel(channel_id, **updates)
        return {"success": True}

    @app.post("/api/agents/{agent_id}/channels/{channel_id}/fix")
    async def api_fix_agent_channel(agent_id: str, channel_id: str, user: str = Depends(_require_login)) -> dict:
        """Try to create the Slack channel if external_channel_id is missing."""
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        channel = database.get_channel(channel_id)
        if not channel:
            raise HTTPException(status_code=404, detail="Channel not found")
        if channel.type != "slack":
            raise HTTPException(status_code=400, detail="Can only fix Slack channels")
        if channel.external_channel_id:
            return {"success": True, "external_channel_id": channel.external_channel_id, "message": "Channel already has external ID"}
        external_channel_id, slack_error = _create_agent_slack_channel(database, agent, channel.name)
        if slack_error and not external_channel_id:
            raise HTTPException(status_code=400, detail=slack_error)
        if external_channel_id:
            database.update_channel(channel_id, external_channel_id=external_channel_id)
        return {"success": True, "external_channel_id": external_channel_id}

    # Generic model API routes (catch-all, must be after specific routes)
    @app.get("/api/{model}/{item_id:path}")
    async def api_get(model: str, item_id: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "read")
        record = handlers["get"](item_id)
        if not record:
            raise HTTPException(status_code=404, detail="Not found")
        if isinstance(record, list):
            if not record:
                raise HTTPException(status_code=404, detail="Not found")
            record = record[0]
        return {"data": _record_to_dict(record)}

    @app.put("/api/{model}/{item_id:path}")
    async def api_update_put(model: str, item_id: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "update")
        payload = await request.json()
        try:
            handlers["update"](item_id, payload)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=400, detail=repr(exc)) from exc
        return {"ok": True}

    @app.delete("/api/{model}/{item_id:path}")
    async def api_delete(model: str, item_id: str, request: Request) -> dict[str, Any]:
        handlers = _api_model_handlers().get(model)
        if not handlers:
            raise HTTPException(status_code=404, detail="Unknown model")
        _require_api_permission(request, model, "delete")
        handlers["delete"](item_id)
        return {"ok": True}

    @app.post("/projects")
    async def create_project(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        slug_raw = str(form.get("slug", "")).strip()
        return_to = str(form.get("return_to", "/ui/projects")).strip() or "/ui/projects"
        if not return_to.startswith("/ui"):
            return_to = "/ui/projects"
        if not name:
            raise HTTPException(status_code=400, detail="Missing project name")
        slug = slug_raw or _slugify(name)
        channel_name = slug
        channel_id = None
        prompt_template = str(form.get("prompt_template", "")).strip() or None
        max_repo_raw = str(form.get("max_repo_resources", "")).strip()
        try:
            max_repo_resources = int(max_repo_raw) if max_repo_raw else 3
        except ValueError:
            max_repo_resources = 3
        channel_id_raw = str(form.get("slack_channel_id", "")).strip()
        if channel_id_raw:
            channel_id = channel_id_raw
        repo_mode = str(form.get("repo_mode", "")).strip() or None
        repo_path = str(form.get("repo_path", "")).strip() or None
        repo_url = str(form.get("repo_url", "")).strip() or None
        slack_bot = database.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        if not channel_id and slack_bot:
            client = _slack_client(database)
            try:
                resp = client.conversations_create(name=channel_name, is_private=False)
                channel_id = resp.get("channel", {}).get("id")
            except Exception as exc:
                error_text = str(exc)
                if "name_taken" in error_text:
                    channel_id = _find_channel_id(client, channel_name)
                elif "missing_scope" in error_text:
                    logging.getLogger(__name__).warning("Slack channel create missing scope; continuing without channel id.")
                else:
                    logging.getLogger(__name__).warning("Slack channel create failed; continuing without channel id: %s", exc)
        if channel_id and slack_bot:
            admin_user_id = _slack_admin_user_id(database)
            client = _slack_client(database)
            try:
                client.conversations_join(channel=channel_id)
            except Exception:
                pass
            if admin_user_id:
                invited = False
                try:
                    client.conversations_invite(channel=channel_id, users=admin_user_id)
                    invited = True
                except Exception:
                    try:
                        client.channels_invite(channel=channel_id, user=admin_user_id)
                        invited = True
                    except Exception:
                        invited = False
                if not invited:
                    client.chat_postMessage(
                        channel=channel_id,
                        text="Channel created. Please /join to receive updates.",
                    )
            else:
                client.chat_postMessage(
                    channel=channel_id,
                    text="Channel created. Please /join to receive updates.",
                )
        # Issue source fields
        provider = str(form.get("provider", "")).strip() or None
        source_repo_raw = str(form.get("source_repo", "")).strip()
        source_repo = _normalize_source_repo(provider or "", source_repo_raw) if source_repo_raw else None
        source_token_id = str(form.get("source_token_id", "")).strip() or None
        source_agent_id = str(form.get("source_agent_id", "")).strip() or None
        issue_state = str(form.get("issue_state", "")).strip() or None
        issue_labels_raw = str(form.get("issue_labels", "")).strip()
        issue_labels = [label.strip() for label in issue_labels_raw.split(",") if label.strip()] if issue_labels_raw else []
        source_enabled = form.get("source_enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        project_id = str(uuid.uuid4())
        master_branch_name = str(form.get("master_branch_name", "")).strip() or "master"
        build_status_image_url = str(form.get("build_status_image_url", "")).strip() or None
        database.insert_project(
            project_id,
            name,
            slug,
            channel_id,
            prompt_template,
            max_repo_resources,
            repo_mode=repo_mode,
            repo_path=repo_path,
            repo_url=repo_url,
            master_branch_name=master_branch_name,
            build_status_image_url=build_status_image_url,
            provider=provider,
            source_token_id=source_token_id,
            source_agent_id=source_agent_id,
            source_repo=source_repo,
            issue_state=issue_state,
            issue_labels=issue_labels,
            source_enabled=source_enabled,
            auto_start=auto_start,
        )
        _update_slack_channel_filter(database)
        return RedirectResponse(f"{return_to}?saved=project_created", status_code=303)

    @app.get("/ui/projects/{project_id}/edit")
    def edit_project_ui(project_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        remote_tokens = database.list_remote_tokens()
        agents = database.list_agents()
        mirror_repo_path_base = os.environ.get("WINTERMUTE_MIRROR_REPO_PATH_BASE", "/home/user/git")
        # Compute external repo URL and build badge from project's source settings
        external_repo_url = None
        external_repo_provider = None
        default_build_badge_url = None
        build_badge_url = None
        build_badge_link = None
        if project.provider and project.source_repo:
            branch = project.master_branch_name or "master"
            if project.provider == "github":
                external_repo_url = f"https://github.com/{project.source_repo}"
                external_repo_provider = "github"
                # GitHub Actions badge (default)
                default_build_badge_url = f"https://github.com/{project.source_repo}/actions/workflows/ci.yml/badge.svg?branch={branch}"
                build_badge_link = f"https://github.com/{project.source_repo}/actions"
            elif project.provider == "gitlab":
                gitlab_base = "https://gitlab.com"
                if project.source_token_id:
                    token = database.get_remote_token(project.source_token_id)
                    if token and token.base_url:
                        gitlab_base = token.base_url.rstrip("/")
                external_repo_url = f"{gitlab_base}/{project.source_repo}"
                external_repo_provider = "gitlab"
                # GitLab CI badge - use proxy to avoid referrer blocking
                default_build_badge_url = f"{gitlab_base}/{project.source_repo}/badges/{branch}/pipeline.svg"
                build_badge_link = f"{gitlab_base}/{project.source_repo}/-/pipelines"
            # Use custom badge URL if set, otherwise use proxy for GitLab or direct for GitHub
            if project.build_status_image_url:
                build_badge_url = project.build_status_image_url
            elif project.provider == "gitlab":
                build_badge_url = f"/badges/projects/{project.id}"
            else:
                build_badge_url = default_build_badge_url
        return _render_template(
            request,
            "project_edit.html",
            {
                "title": "Edit Project",
                "active_nav": "projects",
                "growl_message": None,
                "project": project,
                "default_prompt_template": DEFAULT_PROJECT_PROMPT_TEMPLATE,
                "remote_tokens": remote_tokens,
                "agents": agents,
                "external_repo_url": external_repo_url,
                "external_repo_provider": external_repo_provider,
                "build_badge_url": build_badge_url,
                "build_badge_link": build_badge_link,
                "default_build_badge_url": default_build_badge_url,
                "mirror_repo_path_base": mirror_repo_path_base,
            },
        )

    @app.post("/projects/{project_id}/sources")
    async def create_project_source(project_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        form = await request.form()
        return_to = str(form.get("return_to", f"/ui/projects/{project_id}/edit")).strip() or f"/ui/projects/{project_id}/edit"
        provider = str(form.get("provider", "")).strip().lower()
        if provider not in {"github", "gitlab"}:
            raise HTTPException(status_code=400, detail="Invalid source provider")
        repo_input = str(form.get("repo", "")).strip()
        repo = _normalize_source_repo(provider, repo_input)
        if not repo:
            raise HTTPException(status_code=400, detail="Source repo missing")
        token_id = str(form.get("token_id", "")).strip() or None
        if token_id:
            if provider == "github" and not database.get_github_token(token_id):
                raise HTTPException(status_code=400, detail="GitHub token not found")
            if provider == "gitlab" and not database.get_gitlab_token(token_id):
                raise HTTPException(status_code=400, detail="GitLab token not found")
        agent_id = str(form.get("agent_id", "")).strip() or None
        state = str(form.get("state", "open")).strip() or "open"
        labels_raw = str(form.get("labels", "")).strip()
        labels = _parse_labels(labels_raw)
        enabled = form.get("enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        database.insert_issue_source(
            source_id=str(uuid.uuid4()),
            provider=provider,
            token_id=token_id,
            agent_id=agent_id,
            project_id=project.id,
            repo=repo,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
        )
        return RedirectResponse(f"{return_to}?saved=project_source_created", status_code=303)

    @app.post("/projects/{project_id}")
    async def update_project(project_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        form = await request.form()
        name = str(form.get("name", "")).strip()
        slug = str(form.get("slug", "")).strip()
        channel_id = str(form.get("slack_channel_id", "")).strip() or None
        prompt_template = str(form.get("prompt_template", "")).strip() or None
        max_repo_raw = str(form.get("max_repo_resources", "")).strip()
        try:
            max_repo_resources = int(max_repo_raw) if max_repo_raw else project.max_repo_resources
        except ValueError:
            max_repo_resources = project.max_repo_resources
        repo_mode = str(form.get("repo_mode", "")).strip() or None
        repo_path = str(form.get("repo_path", "")).strip() or None
        repo_url = str(form.get("repo_url", "")).strip() or None
        # Issue source fields
        provider = str(form.get("provider", "")).strip() or None
        source_repo_raw = str(form.get("source_repo", "")).strip()
        source_repo = _normalize_source_repo(provider or "", source_repo_raw) if source_repo_raw else None
        source_token_id = str(form.get("source_token_id", "")).strip() or None
        source_agent_id = str(form.get("source_agent_id", "")).strip() or None
        issue_state = str(form.get("issue_state", "")).strip() or None
        issue_labels_raw = str(form.get("issue_labels", "")).strip()
        issue_labels = [label.strip() for label in issue_labels_raw.split(",") if label.strip()] if issue_labels_raw else []
        source_enabled = form.get("source_enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        if not name or not slug:
            raise HTTPException(status_code=400, detail="Missing name or slug")
        master_branch_name = str(form.get("master_branch_name", "")).strip() or "master"
        build_status_image_url = str(form.get("build_status_image_url", "")).strip() # Empty clears the field
        database.update_project(
            project_id,
            name=name,
            slug=slug,
            slack_channel_id=channel_id,
            prompt_template=prompt_template,
            max_repo_resources=max_repo_resources,
            repo_mode=repo_mode,
            repo_path=repo_path,
            repo_url=repo_url,
            master_branch_name=master_branch_name,
            build_status_image_url=build_status_image_url,
            provider=provider,
            source_token_id=source_token_id,
            source_agent_id=source_agent_id,
            source_repo=source_repo,
            issue_state=issue_state,
            issue_labels=issue_labels,
            source_enabled=source_enabled,
            auto_start=auto_start,
        )
        _update_slack_channel_filter(database)
        return RedirectResponse("/ui/projects?saved=project_updated", status_code=303)

    @app.post("/projects/{project_id}/delete")
    async def delete_project(project_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        confirm = str(form.get("confirm", "")).strip().lower()
        delete_slack = form.get("delete_slack") == "on"
        if confirm != "delete me":
            raise HTTPException(status_code=400, detail="Confirmation text mismatch")
        if delete_slack:
            project = database.get_project(project_id)
            if project and project.slack_channel_id:
                client = _slack_client(database)
                try:
                    client.conversations_archive(channel=project.slack_channel_id)
                except Exception as exc:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Slack channel delete failed: {exc}",
                    ) from exc
        database.delete_project(project_id)
        _update_slack_channel_filter(database)
        return RedirectResponse("/ui/projects?saved=project_deleted", status_code=303)

    @app.get("/badges/projects/{project_id}")
    async def get_project_badge(project_id: str) -> Response:
        """Return CI badge image for project. Caches GitLab status for 5 minutes."""
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")

        if not project.provider or not project.source_repo:
            raise HTTPException(status_code=404, detail="Project has no source configured")

        branch = project.master_branch_name or "master"

        # GitHub: redirect directly (no referrer issues)
        if project.provider == "github":
            if project.build_status_image_url:
                badge_url = project.build_status_image_url
            else:
                badge_url = f"https://github.com/{project.source_repo}/actions/workflows/ci.yml/badge.svg?branch={branch}"
            return RedirectResponse(badge_url, status_code=302)

        # GitLab: fetch status via API, return shields.io badge
        if project.provider == "gitlab":
            cache_key = f"{project_id}:{branch}"
            now = time.time()

            # Check cache
            if cache_key in _badge_cache:
                cached_status, cached_time = _badge_cache[cache_key]
                if now - cached_time < BADGE_CACHE_TTL:
                    color = {"success": "brightgreen", "failed": "red", "running": "yellow", "pending": "yellow"}.get(cached_status, "lightgrey")
                    shields_url = f"https://img.shields.io/badge/pipeline-{cached_status}-{color}"
                    return RedirectResponse(shields_url, status_code=302)

            # Fetch from GitLab API
            token_record = database.get_remote_token(project.source_token_id) if project.source_token_id else None
            if not token_record:
                raise HTTPException(status_code=400, detail="GitLab token not configured for project")

            gitlab_base = token_record.base_url.rstrip("/") if token_record.base_url else "https://gitlab.com"
            api_base = f"{gitlab_base}/api/v4"
            encoded_repo = urllib.parse.quote(project.source_repo, safe="")
            url = f"{api_base}/projects/{encoded_repo}/pipelines?ref={branch}&per_page=1"

            headers = {
                "Accept": "application/json",
                "PRIVATE-TOKEN": token_record.token,
                "User-Agent": "wintermute",
            }

            status = "unknown"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, headers=headers) as response:
                        if response.status == 200:
                            pipelines = await response.json()
                            if pipelines and len(pipelines) > 0:
                                status = pipelines[0].get("status", "unknown")
            except Exception as exc:
                logging.getLogger(__name__).warning("Failed to fetch GitLab pipeline status: %s", exc)
                status = "error"

            # Cache the status
            _badge_cache[cache_key] = (status, now)

            color = {
                "success": "brightgreen",
                "failed": "red",
                "running": "yellow",
                "pending": "yellow",
                "canceled": "lightgrey",
                "skipped": "lightgrey"
            }.get(status, "lightgrey")
            shields_url = f"https://img.shields.io/badge/pipeline-{status}-{color}"
            return RedirectResponse(shields_url, status_code=302)

        raise HTTPException(status_code=400, detail=f"Unsupported provider: {project.provider}")

    @app.post("/tickets")
    async def create_ticket(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        agent_id = str(form.get("agent_id", "")).strip() or None
        title = str(form.get("title", "")).strip()
        description = str(form.get("description", "")).strip() or None
        internal_notes = str(form.get("internal_notes", "")).strip() or None
        assigned_to = str(form.get("assigned_to", "")).strip() or None
        estimate = str(form.get("estimate", "")).strip() or None
        status = str(form.get("status", "open")).strip() or "open"
        source_url = str(form.get("source_url", "")).strip() or None
        auto_start = form.get("auto_start") == "on"
        sprint_id = str(form.get("sprint_id", "")).strip() or None
        priority = str(form.get("priority", "")).strip() or None
        hours_str = str(form.get("hours", "")).strip()
        hours = float(hours_str) if hours_str else None
        story_points_str = str(form.get("story_points", "")).strip()
        story_points = float(story_points_str) if story_points_str else None
        return_to = str(form.get("return_to", "/ui/tickets")).strip() or "/ui/tickets"
        if not return_to.startswith("/ui"):
            return_to = "/ui/tickets"
        if not project_id or not title:
            raise HTTPException(status_code=400, detail="Missing project or title")
        user_record = database.get_user(user)
        created_by_id = user_record.id if user_record else None
        database.insert_ticket(
            ticket_id=str(uuid.uuid4()),
            project_id=project_id,
            agent_id=agent_id,
            title=title,
            description=description,
            assigned_to=assigned_to,
            estimate=estimate,
            status=status,
            internal_notes=internal_notes,
            source_url=source_url,
            auto_start=auto_start,
            sprint_id=sprint_id,
            priority=priority,
            hours=hours,
            story_points=story_points,
            created_by_id=created_by_id,
        )
        return RedirectResponse(f"{return_to}?saved=ticket_created", status_code=303)

    @app.post("/tickets/{ticket_id}/delete")
    async def delete_ticket(ticket_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_ticket(ticket_id)
        return RedirectResponse("/ui/tickets?saved=ticket_deleted", status_code=303)

    @app.get("/ui/tickets/{ticket_id}/edit")
    async def tickets_edit_ui(ticket_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        growl_message = _growl_message(request.query_params.get("saved"))
        session = database.get_session_by_ticket(ticket_id)
        session_running = bool(session and session.status == "running")
        provider, source_id, issue_number = parse_issue_ticket(ticket_id)
        is_github_ticket = provider == "github"
        is_gitlab_ticket = provider == "gitlab"
        is_external_ticket = provider is not None
        description_html = _render_markdown(ticket.description)
        github_comments: list[dict[str, Any]] = []
        github_comments_error: Optional[str] = None
        github_comments_cached = False
        source_record = None
        source_label = None
        source_href = None
        if is_github_ticket:
            source_record = database.get_github_source(source_id) if source_id else None
            if source_record:
                source_label = f"{source_record.owner}/{source_record.repo}"
                source_href = f"/ui/issue-sources/{source_record.id}/edit"
        elif is_gitlab_ticket:
            source_record = database.get_gitlab_source(source_id) if source_id else None
            if source_record:
                source_label = source_record.project_path
                source_href = f"/ui/issue-sources/{source_record.id}/edit"
        if source_record and ticket.project_id != source_record.project_id:
            database.update_ticket(ticket.id, project_id=source_record.project_id)
            ticket = database.get_ticket(ticket.id) or ticket
        mapping_project_id = source_record.project_id if source_record else ticket.project_id
        location_label = "none"
        ticket_agent = database.get_agent(ticket.agent_id) if ticket.agent_id else None
        if session_running and session:
            session_agent = database.get_agent(session.agent_id)
            vm_target = database.get_vm_target(session_agent.vm_target_id) if session_agent and session_agent.vm_target_id else None
            if vm_target:
                location_label = vm_target.name
        start_ready = True
        start_reason = ""
        if is_github_ticket and not source_record:
            start_ready = False
            start_reason = "GitHub source is missing for this ticket."
        if is_gitlab_ticket and not source_record:
            start_ready = False
            start_reason = "GitLab source is missing for this ticket."
        agent_id = ticket.agent_id or (source_record.agent_id if source_record else None)
        if not agent_id:
            start_ready = False
            start_reason = "No agent assigned to this ticket."
        else:
            check_agent = database.get_agent(agent_id)
            if check_agent and not check_agent.vm_target_id:
                start_ready = False
                start_reason = "Agent has no VM target configured."
        if is_github_ticket and source_id and issue_number is not None:
            source = database.get_github_source(source_id)
            if source and source.token_id:
                token_record = database.get_github_token(source.token_id)
                if token_record:
                    (
                        github_comments,
                        github_comments_error,
                        github_comments_cached,
                    ) = await _get_github_comments_cached(
                        database,
                        ticket,
                        token_record.token,
                        source.owner,
                        source.repo,
                        issue_number,
                    )
        comment_rows = list(reversed(database.list_comments(ticket_id=ticket_id)))
        comments = [{
            **_comment_to_dict(row),
            "created_at": row.created_at,
        } for row in comment_rows]
        last_comment_ts = comment_rows[-1].created_at if comment_rows else None
        created_by_user = database.get_user_by_id(ticket.created_by_id) if ticket.created_by_id else None
        return _render_template(
            request,
            "ticket_edit.html",
            {
                "title": "Edit Ticket",
                "active_nav": "tickets",
                "growl_message": growl_message,
                "ticket": ticket,
                "projects": database.list_projects(),
                "agents": database.list_agents(),
                "users": database.list_users(),
                "sprints": database.list_sprints(),
                "description_html": description_html,
                "is_github_ticket": is_github_ticket,
                "is_gitlab_ticket": is_gitlab_ticket,
                "is_external_ticket": is_external_ticket,
                "start_ready": start_ready,
                "start_reason": start_reason,
                "location_label": location_label,
                "comments": comments,
                "last_comment_ts": last_comment_ts,
                "github_comments": github_comments,
                "github_comments_error": github_comments_error,
                "github_comments_cached": github_comments_cached,
                "github_comments_fetched_at": ticket.github_comments_fetched_at,
                "session_running": session_running,
                "session_id": session.id if session else None,
                "session_repo_path": session.repo_path if session else None,
                "source_record": source_record,
                "source_label": source_label,
                "source_href": source_href,
                "created_by_user": created_by_user,
            },
        )

    @app.post("/tickets/{ticket_id}/edit")
    async def update_ticket(ticket_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        agent_id = str(form.get("agent_id", "")).strip()
        title = str(form.get("title", "")).strip()
        description = str(form.get("description", "")).strip() or None
        internal_notes = str(form.get("internal_notes", "")).strip() or None
        assigned_to = str(form.get("assigned_to", "")).strip() or None
        estimate = str(form.get("estimate", "")).strip() or None
        status = str(form.get("status", "open")).strip() or "open"
        source_url = str(form.get("source_url", "")).strip() or None
        auto_start = form.get("auto_start") == "on"
        sprint_id = str(form.get("sprint_id", "")).strip() or None
        priority = str(form.get("priority", "")).strip() or None
        hours_str = str(form.get("hours", "")).strip()
        hours = float(hours_str) if hours_str else None
        story_points_str = str(form.get("story_points", "")).strip()
        story_points = float(story_points_str) if story_points_str else None
        if not project_id or not title:
            raise HTTPException(status_code=400, detail="Missing ticket fields")
        database.update_ticket(
            ticket_id,
            project_id=project_id,
            agent_id=agent_id,
            title=title,
            description=description,
            internal_notes=internal_notes,
            assigned_to=assigned_to,
            estimate=estimate,
            status=status,
            source_url=source_url,
            auto_start=auto_start,
            sprint_id=sprint_id,
            priority=priority,
            hours=hours,
            story_points=story_points,
        )
        return RedirectResponse(f"/ui/tickets/{ticket_id}/edit?saved=ticket_updated", status_code=303)

    @app.post("/api/tickets/{ticket_id}/description")
    async def api_ticket_update_description(ticket_id: str, request: Request, user: str = Depends(_require_login)) -> dict[str, Any]:
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        provider, _source_id, _issue_number = parse_issue_ticket(ticket.id)
        if provider:
            raise HTTPException(status_code=400, detail="External ticket descriptions are read-only")
        payload = await request.json()
        description = str(payload.get("description", ""))
        database.update_ticket(ticket_id, description=description)
        return {"ok": True, "html": _render_markdown(description)}

    @app.patch("/api/tickets/{ticket_id}")
    async def api_ticket_patch(ticket_id: str, request: Request) -> dict[str, Any]:
        _require_login_or_api(request, "tickets", "update")
        ticket = database.get_ticket(ticket_id)
        if not ticket:
            raise HTTPException(status_code=404, detail="Ticket not found")
        payload = await request.json()
        updates: dict[str, Any] = {}
        if "status" in payload:
            status = str(payload["status"]).strip()
            if status not in ("open", "in-progress", "needs-feedback", "done"):
                raise HTTPException(status_code=400, detail="Invalid status")
            updates["status"] = status
        if "priority" in payload:
            priority = payload["priority"]
            if priority is not None:
                priority = str(priority).strip() or None
                if priority and priority not in ("low", "medium", "high"):
                    raise HTTPException(status_code=400, detail="Invalid priority")
            updates["priority"] = priority
        if "story_points" in payload:
            sp = payload["story_points"]
            if sp is not None and sp != "":
                try:
                    updates["story_points"] = float(sp)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid story_points")
            else:
                updates["clear_story_points"] = True
        if "priority" in payload and updates.get("priority") is None:
            updates["clear_priority"] = True
            del updates["priority"]
        if not updates:
            raise HTTPException(status_code=400, detail="No valid fields to update")
        database.update_ticket(ticket_id, **updates)
        updated = database.get_ticket(ticket_id)
        return {
            "ok": True,
            "ticket": {
                "id": updated.id,
                "status": updated.status,
                "priority": updated.priority,
                "story_points": updated.story_points,
            },
        }

    @app.get("/ui/comments")
    def comments_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        comments = database.list_comments()
        growl_message = _growl_message(request.query_params.get("saved"))
        projects = database.list_projects()
        agents = database.list_agents()
        project_lookup = {project.id: project.name for project in projects}
        agent_lookup = {agent.id: agent.name for agent in agents}
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="comments",
            title="Comments",
            description="All captured agent comments.",
            create_label=None,
            create_url=None,
            rows=_build_comment_rows(comments, project_lookup, agent_lookup),
            empty_message="No comments yet.",
        )
        return _render_template(
            request,
            "comments.html",
            {
                "title": "Comments",
                "active_nav": "comments",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/comments/{comment_id}/edit")
    def comment_edit_ui(comment_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        comment = database.get_comment(comment_id)
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "comment_edit.html",
            {
                "title": "Edit Comment",
                "active_nav": "comments",
                "growl_message": growl_message,
                "comment": comment,
                "tickets": database.list_tickets(),
                "project_lookup": {
                    project.id: project.name
                    for project in database.list_projects()
                },
                "agent_lookup": {
                    agent.id: agent.name
                    for agent in database.list_agents()
                },
            },
        )

    @app.post("/comments/{comment_id}/edit")
    async def update_comment(comment_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        body = str(form.get("body", "")).strip()
        public = form.get("public") == "on"
        approved = form.get("approved") == "on"
        if not body:
            raise HTTPException(status_code=400, detail="Missing comment body")
        database.update_comment(
            comment_id,
            body=body,
            public=public,
            approved=approved,
        )
        return RedirectResponse(f"/ui/comments/{comment_id}/edit?saved=comment_updated", status_code=303)

    @app.post("/comments/{comment_id}/delete")
    async def delete_comment(comment_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_comment(comment_id)
        return RedirectResponse("/ui/comments?saved=comment_deleted", status_code=303)

    @app.get("/ui/repo-resources")
    def repo_resources_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        resources = database.list_repo_resources()
        project_lookup = {row.id: row.name for row in database.list_projects()}
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="repo_resources",
            title="Repo Resources",
            description="Auto-managed repo working trees by project/VM.",
            create_label=None,
            create_url=None,
            rows=_build_repo_resource_rows(resources, project_lookup),
            empty_message="No repo resources yet.",
        )
        return _render_template(
            request,
            "repo_resources.html",
            {
                "title": "Repo Resources",
                "active_nav": "repo_resources",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/repo-resources/{resource_id}/edit")
    def repo_resource_edit_ui(resource_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        resource = database.get_repo_resource(resource_id)
        if not resource:
            raise HTTPException(status_code=404, detail="Repo resource not found")
        projects = database.list_projects()
        project_lookup = {row.id: row.name for row in projects}
        return _render_template(
            request,
            "repo_resource_edit.html",
            {
                "title": "Repo Resource",
                "active_nav": "repo_resources",
                "growl_message": _growl_message(request.query_params.get("saved")),
                "resource": resource,
                "project_lookup": project_lookup,
            },
        )

    @app.post("/repo-resources/{resource_id}")
    async def repo_resource_update(resource_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        status = str(form.get("status", "")).strip() or None
        session_id = str(form.get("session_id", "")).strip() or None
        agent_id = str(form.get("agent_id", "")).strip() or None
        database.update_repo_resource(
            resource_id,
            status=status,
            session_id=session_id,
            agent_id=agent_id,
            last_used_at=utc_now(),
        )
        return RedirectResponse(f"/ui/repo-resources/{resource_id}/edit?saved=repo_resource_updated", status_code=303)

    @app.post("/repo-resources/{resource_id}/delete")
    async def repo_resource_delete(resource_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_repo_resource(resource_id)
        return RedirectResponse("/ui/repo-resources?saved=repo_resource_deleted", status_code=303)

    @app.post("/vms")
    async def create_vm(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        host = str(form.get("host", "")).strip()
        user_name = str(form.get("user", "")).strip()
        port = int(form.get("port", 22))
        return_to = str(form.get("return_to", "/ui/vms")).strip() or "/ui/vms"
        if not return_to.startswith("/ui"):
            return_to = "/ui/vms"
        if not name or not host or not user_name:
            raise HTTPException(status_code=400, detail="Missing VM fields")
        database.insert_vm_target(str(uuid.uuid4()), name, host, user_name, port)
        return RedirectResponse(f"{return_to}?saved=vm_created", status_code=303)

    @app.get("/ui/vms/{vm_id}/edit")
    def edit_vm_ui(vm_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        vm = database.get_vm_target(vm_id)
        if not vm:
            raise HTTPException(status_code=404, detail="VM not found")
        return _render_template(
            request,
            "vm_edit.html",
            {
                "title": "Edit VM",
                "active_nav": "vms",
                "growl_message": None,
                "vm": vm,
            },
        )

    @app.post("/vms/{vm_id}/edit")
    async def update_vm(vm_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        host = str(form.get("host", "")).strip()
        user_name = str(form.get("user", "")).strip()
        port = int(form.get("port", 22))
        required_reserve_memory_gb_str = str(form.get("required_reserve_memory_gb", "0")).strip()
        try:
            required_reserve_memory_gb = float(required_reserve_memory_gb_str) if required_reserve_memory_gb_str else 0.0
        except ValueError:
            required_reserve_memory_gb = 0.0
        if not name or not host or not user_name:
            raise HTTPException(status_code=400, detail="Missing VM fields")
        database.update_vm_target(
            vm_id,
            name=name,
            host=host,
            user=user_name,
            port=port,
            required_reserve_memory_gb=required_reserve_memory_gb,
        )
        return RedirectResponse("/ui/vms?saved=vm_updated", status_code=303)

    @app.post("/vms/{vm_id}/delete")
    async def delete_vm(vm_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_vm_target(vm_id)
        return RedirectResponse("/ui/vms?saved=vm_deleted", status_code=303)

    @app.post("/agents")
    async def create_agent(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        slug = str(form.get("slug", "")).strip()
        command = str(form.get("command", "")).strip()
        session_mode = str(form.get("session_mode", "tmux")).strip() or "tmux"
        vm_target_id = str(form.get("vm_target_id", "")).strip() or None
        ssh_options = str(form.get("required_ssh_options", "")).strip() or None
        env_vars = str(form.get("env_vars", "")).strip() or None
        mcp_config = str(form.get("mcp_config", "")).strip() or None
        trust_level = str(form.get("trust_level", "")).strip() or None
        input_echo_prefix = str(form.get("input_echo_prefix", "")).strip() or None
        response_prefix = str(form.get("response_prefix", "")).strip() or None
        llm_base_url = str(form.get("llm_base_url", "")).strip() or None
        llm_api_key = str(form.get("llm_api_key", "")).strip() or None
        llm_model = str(form.get("llm_model", "")).strip() or None
        working_directory = str(form.get("working_directory", "")).strip() or None
        session_directory = str(form.get("session_directory", "")).strip() or None
        return_to = str(form.get("return_to", "/ui/agents")).strip() or "/ui/agents"
        if not return_to.startswith("/ui"):
            return_to = "/ui/agents"
        if not name or not slug or not command:
            raise HTTPException(status_code=400, detail="Missing agent fields")
        try:
            database.insert_agent(
                str(uuid.uuid4()),
                name,
                slug,
                command,
                session_mode,
                vm_target_id,
                ssh_options,
                env_vars,
                mcp_config,
                trust_level,
                input_echo_prefix,
                response_prefix,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                working_directory=working_directory,
                session_directory=session_directory,
            )
        except IntegrityError as exc:
            field, message = _parse_integrity_error(exc)
            error_param = urllib.parse.quote(f"{field}:{message}" if field else message)
            return RedirectResponse(f"/ui/agents/create?error={error_param}&return_to={urllib.parse.quote(return_to)}", status_code=303)
        return RedirectResponse(f"{return_to}?saved=agent_created", status_code=303)

    @app.post("/ui/agents/bulk-action")
    async def agents_bulk_action(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        """Handle bulk actions on agents (e.g., clone)."""
        form = await request.form()
        action = str(form.get("action", "")).strip()
        ids = form.getlist("ids")
        if not action or not ids:
            raise HTTPException(status_code=400, detail="Missing action or ids")

        cloned_count = 0
        if action == "clone":
            # Get all existing agents to check for unique name/slug conflicts
            existing_agents = database.list_agents()
            existing_names = {a.name for a in existing_agents}
            existing_slugs = {a.slug for a in existing_agents}

            for agent_id in ids:
                agent = database.get_agent(agent_id)
                if not agent:
                    continue

                # Generate unique name
                new_name = _generate_unique_string(agent.name, existing_names)
                existing_names.add(new_name)

                # Generate unique slug
                new_slug = _generate_unique_string(agent.slug, existing_slugs)
                existing_slugs.add(new_slug)

                # Create the clone with new UUID
                database.insert_agent(
                    agent_id=str(uuid.uuid4()),
                    name=new_name,
                    slug=new_slug,
                    command=agent.command,
                    session_mode=agent.session_mode,
                    vm_target_id=agent.vm_target_id,
                    required_ssh_options=agent.required_ssh_options,
                    env_vars=agent.env_vars,
                    mcp_config=agent.mcp_config,
                    trust_level=agent.trust_level,
                    input_echo_prefix=agent.input_echo_prefix,
                    response_prefix=agent.response_prefix,
                    llm_base_url=agent.llm_base_url,
                    llm_api_key=agent.llm_api_key,
                    llm_model=agent.llm_model,
                    average_memory_usage_mb=agent.average_memory_usage_mb,
                    working_directory=agent.working_directory,
                    session_directory=agent.session_directory,
                )
                cloned_count += 1

            return RedirectResponse(f"/ui/agents?saved=cloned_{cloned_count}", status_code=303)

        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    @app.get("/ui/agents/{agent_id}/edit")
    def edit_agent_ui(agent_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        responses = database.list_agent_responses(agent_id=agent_id)
        vm_targets = database.list_vm_targets()
        channels = database.list_channels(agent_id=agent_id)
        session_file_configs = database.list_session_file_configs()
        # Get session files for this agent
        session_files = database.list_session_files(agent_id)
        # Get file definitions if agent has a config
        session_file_definitions = []
        if agent.session_file_config_id:
            session_file_definitions = database.list_session_file_definitions(agent.session_file_config_id)
        # Build map of definition_id -> session file content
        file_contents = {sf.definition_id: sf for sf in session_files}
        # Get standalone sessions (no ticket_id)
        all_sessions = database.list_sessions(agent_id=agent_id)
        standalone_session = None # Active (running/blocked) session for controls
        standalone_session_ids: list[str] = [] # All standalone session IDs for comments
        for sess in all_sessions:
            if not sess.ticket_id:
                standalone_session_ids.append(sess.id)
                if sess.status in ("running", "blocked") and standalone_session is None:
                    standalone_session = sess
        # Get comments from all standalone sessions (persists across start/stop)
        standalone_comments: list = []
        if standalone_session_ids:
            standalone_comments = database.list_comments(agent_session_ids=standalone_session_ids)

        # Parse error from query params (e.g., "field:message" or just "message")
        error_param = request.query_params.get("error")
        error_field = None
        error_message = None
        if error_param:
            if ":" in error_param:
                error_field, error_message = error_param.split(":", 1)
            else:
                error_message = error_param

        return _render_template(
            request,
            "agent_edit.html",
            {
                "title": "Edit Agent",
                "active_nav": "agents",
                "growl_message": None,
                "agent": agent,
                "responses": responses,
                "vm_targets": vm_targets,
                "channels": channels,
                "session_file_configs": session_file_configs,
                "session_file_definitions": session_file_definitions,
                "file_contents": file_contents,
                "standalone_session": standalone_session,
                "standalone_comments": standalone_comments,
                "error_field": error_field,
                "error_message": error_message,
            },
        )

    @app.post("/agents/{agent_id}/edit")
    async def update_agent(agent_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        slug = str(form.get("slug", "")).strip()
        command = str(form.get("command", "")).strip()
        session_mode = str(form.get("session_mode", "tmux")).strip() or "tmux"
        vm_target_id = str(form.get("vm_target_id", "")).strip() or None
        ssh_options = str(form.get("required_ssh_options", "")).strip() or None
        env_vars = str(form.get("env_vars", "")).strip() or None
        mcp_config = str(form.get("mcp_config", "")).strip() or None
        trust_level = str(form.get("trust_level", "")).strip() or None
        input_echo_prefix = str(form.get("input_echo_prefix", "")).strip() or None
        response_prefix = str(form.get("response_prefix", "")).strip() or None
        llm_base_url = str(form.get("llm_base_url", "")).strip() or None
        llm_api_key = str(form.get("llm_api_key", "")).strip() or None
        llm_model = str(form.get("llm_model", "")).strip() or None
        session_file_config_id = str(form.get("session_file_config_id", "")).strip() or None
        initial_prompt = str(form.get("initial_prompt", "")).strip() or None
        working_directory = str(form.get("working_directory", "")).strip() or None
        session_directory = str(form.get("session_directory", "")).strip() or None
        if not name or not slug or not command:
            raise HTTPException(status_code=400, detail="Missing agent fields")
        try:
            database.update_agent(
                agent_id,
                name=name,
                slug=slug,
                command=command,
                session_mode=session_mode,
                vm_target_id=vm_target_id,
                required_ssh_options=ssh_options,
                env_vars=env_vars,
                mcp_config=mcp_config,
                trust_level=trust_level,
                input_echo_prefix=input_echo_prefix,
                response_prefix=response_prefix,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                session_file_config_id=session_file_config_id,
                initial_prompt=initial_prompt,
                working_directory=working_directory,
                session_directory=session_directory,
            )
        except IntegrityError as exc:
            field, message = _parse_integrity_error(exc)
            error_param = urllib.parse.quote(f"{field}:{message}" if field else message)
            return RedirectResponse(f"/ui/agents/{agent_id}/edit?error={error_param}", status_code=303)
        # Auto-generate session file records if config is set
        if session_file_config_id:
            definitions = database.list_session_file_definitions(session_file_config_id)
            existing_files = database.list_session_files(agent_id)
            existing_def_ids = {sf.definition_id for sf in existing_files}
            for defn in definitions:
                if defn.id not in existing_def_ids:
                    database.upsert_session_file(agent_id, defn.id, defn.default_content)
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=agent_updated", status_code=303)

    @app.post("/agents/{agent_id}/delete")
    async def delete_agent(agent_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_agent(agent_id)
        return RedirectResponse("/ui/agents?saved=agent_deleted", status_code=303)

    # -------------------------------------------------------------------------
    # Agent Channels CRUD
    # -------------------------------------------------------------------------

    @app.post("/agents/{agent_id}/channels")
    async def create_agent_channel(agent_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        channel_type = str(form.get("channel_type", "")).strip()
        name = str(form.get("name", "")).strip()
        external_channel_id = str(form.get("external_channel_id", "")).strip() or None
        if not channel_type or not name:
            raise HTTPException(status_code=400, detail="Missing channel fields")
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Auto-create Slack channel if type is slack and no external_channel_id provided
        if channel_type == "slack" and not external_channel_id:
            external_channel_id, slack_error = _create_agent_slack_channel(database, agent, name)
            if not external_channel_id:
                raise HTTPException(status_code=400, detail=slack_error or "Failed to create Slack channel")
        database.insert_channel(
            channel_id=str(uuid.uuid4()),
            agent_id=agent_id,
            channel_type=channel_type,
            name=name,
            external_channel_id=external_channel_id,
            enabled=True,
        )
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=channel_created", status_code=303)

    @app.post("/agents/{agent_id}/channels/{channel_id}/edit")
    async def update_agent_channel(agent_id: str, channel_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip() or None
        external_channel_id = str(form.get("external_channel_id", "")).strip() or None
        enabled_str = str(form.get("enabled", "")).strip()
        enabled = None if enabled_str == "" else enabled_str.lower() in ("true", "1", "yes")
        database.update_channel(
            channel_id,
            name=name,
            external_channel_id=external_channel_id,
            enabled=enabled,
        )
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=channel_updated", status_code=303)

    @app.post("/agents/{agent_id}/channels/{channel_id}/delete")
    async def delete_agent_channel(agent_id: str, channel_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_channel(channel_id)
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=channel_deleted", status_code=303)

    # -------------------------------------------------------------------------
    # Agent Session Files
    # -------------------------------------------------------------------------

    @app.post("/agents/{agent_id}/session-files")
    async def save_agent_session_files(agent_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        agent = database.get_agent(agent_id)
        if not agent or not agent.session_file_config_id:
            raise HTTPException(status_code=400, detail="Agent has no session file config")
        definitions = database.list_session_file_definitions(agent.session_file_config_id)
        form = await request.form()
        for defn in definitions:
            content = str(form.get(f"file_{defn.id}", ""))
            database.upsert_session_file(agent_id, defn.id, content)
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=session_files_saved", status_code=303)

    # -------------------------------------------------------------------------
    # Agent Standalone Session Control
    # -------------------------------------------------------------------------

    @app.post("/agents/{agent_id}/session/start")
    async def start_agent_standalone_session(agent_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Check if a standalone session already exists
        all_sessions = database.list_sessions(agent_id=agent_id)
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                return RedirectResponse(f"/ui/agents/{agent_id}/edit?error=session_already_running", status_code=303)
        # Create a temp workspace
        import tempfile
        workspace = tempfile.mkdtemp(prefix=f"agent_{agent.slug}_")
        # Create the session
        session_id = str(uuid.uuid4())
        default_initial_prompt = "Read your AGENTS.md file and then wait for further instructions."
        initial_prompt = agent.initial_prompt or default_initial_prompt
        database.insert_session(
            session_id=session_id,
            project_id=None,
            agent_id=agent_id,
            ticket_id=None,
            status="running",
            repo_path=workspace,
            thread_ts=None,
            mcp_conversation_id=None,
            initial_prompt=initial_prompt,
            workspace_path=workspace,
        )
        # Copy session files to workspace if agent has a config
        if agent.session_file_config_id:
            definitions = database.list_session_file_definitions(agent.session_file_config_id)
            session_files = database.list_session_files(agent_id)
            file_map = {sf.definition_id: sf for sf in session_files}
            import os
            for defn in definitions:
                content = file_map.get(defn.id, None)
                if content:
                    file_content = content.content
                else:
                    file_content = defn.default_content
                file_path = os.path.join(workspace, defn.filename)
                with open(file_path, "w") as f:
                    f.write(file_content)
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=session_started", status_code=303)

    @app.post("/agents/{agent_id}/session/stop")
    async def stop_agent_standalone_session(agent_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Find the standalone session
        all_sessions = database.list_sessions(agent_id=agent_id)
        standalone_session = None
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                standalone_session = sess
                break
        if not standalone_session:
            return RedirectResponse(f"/ui/agents/{agent_id}/edit?error=no_session", status_code=303)
        # Sync files back from workspace
        if agent.session_file_config_id and standalone_session.workspace_path:
            import os
            definitions = database.list_session_file_definitions(agent.session_file_config_id)
            for defn in definitions:
                if defn.sync_on_exit:
                    file_path = os.path.join(standalone_session.workspace_path, defn.filename)
                    if os.path.exists(file_path):
                        with open(file_path, "r") as f:
                            content = f.read()
                        database.upsert_session_file(agent_id, defn.id, content)
        # Mark session as stopped
        database.update_session(standalone_session.id, status="stopped")
        return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=session_stopped", status_code=303)

    @app.post("/agents/{agent_id}/session/message")
    async def send_agent_standalone_message(agent_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        # Find the standalone session
        all_sessions = database.list_sessions(agent_id=agent_id)
        standalone_session = None
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                standalone_session = sess
                break
        if not standalone_session:
            return RedirectResponse(f"/ui/agents/{agent_id}/edit?error=no_session", status_code=303)
        form = await request.form()
        message = str(form.get("message", "")).strip()
        if not message:
            return RedirectResponse(f"/ui/agents/{agent_id}/edit", status_code=303)
        # Insert as comment
        database.insert_comment(
            comment_id=str(uuid.uuid4()),
            ticket_id=None,
            session_id=standalone_session.id,
            project_id=None,
            agent_id=agent_id,
            author=user,
            source_id=None,
            issue_number=None,
            body=message,
            public=False,
            approved=False,
            agent_session_id=standalone_session.id,
            origin="web",
        )
        # Relay to Slack channels
        try:
            from wintermute.chat.dispatcher import ChatDispatcher
            dispatcher = ChatDispatcher(database)
            import asyncio
            slack_message = f"[{user}] {message}"
            asyncio.create_task(dispatcher.broadcast_to_agent_channels(agent_id, slack_message, platform_filter="slack"))
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to relay to Slack: %s", e)
        return RedirectResponse(f"/ui/agents/{agent_id}/edit", status_code=303)

    @app.post("/agent-responses")
    async def create_agent_response(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        agent_id = str(form.get("agent_id", "")).strip()
        pattern = str(form.get("pattern", "")).strip()
        response = str(form.get("response", "")).strip()
        return_to = str(form.get("return_to", "/ui/agent-responses")).strip() or "/ui/agent-responses"
        if not return_to.startswith("/ui"):
            return_to = "/ui/agent-responses"
        if not agent_id or not pattern or not response:
            raise HTTPException(status_code=400, detail="Missing response fields")
        database.insert_agent_response(str(uuid.uuid4()), agent_id, pattern, response)
        return RedirectResponse(f"{return_to}?saved=agent_response_created", status_code=303)

    @app.post("/agent-responses/{response_id}/edit")
    async def update_agent_response(response_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        agent_id = str(form.get("agent_id", "")).strip()
        pattern = str(form.get("pattern", "")).strip()
        response = str(form.get("response", "")).strip()
        return_to = str(form.get("return_to", "/ui/agent-responses")).strip() or "/ui/agent-responses"
        if not return_to.startswith("/ui"):
            return_to = "/ui/agent-responses"
        if not agent_id or not pattern or not response:
            raise HTTPException(status_code=400, detail="Missing response fields")
        database.update_agent_response(
            response_id,
            agent_id=agent_id,
            pattern=pattern,
            response=response,
        )
        return RedirectResponse(f"{return_to}?saved=agent_response_updated", status_code=303)

    @app.post("/agent-responses/{response_id}/delete")
    async def delete_agent_response(response_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_agent_response(response_id)
        return RedirectResponse("/ui/agent-responses?saved=agent_response_deleted", status_code=303)

    # Legacy project_vms POST handlers - no longer functional
    @app.post("/project_vms")
    async def create_project_vm_legacy(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/agents?saved=project_vms_deprecated", status_code=303)

    @app.post("/project_vms/{mapping_id}/delete")
    async def delete_project_vm_legacy(mapping_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/agents?saved=project_vms_deprecated", status_code=303)

    @app.get("/ui/sessions")
    def sessions_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        sessions = database.list_sessions()
        growl_message = _growl_message(request.query_params.get("saved"))
        project_lookup = {project.id: project.name for project in database.list_projects()}
        agent_lookup = {agent.id: agent.name for agent in database.list_agents()}
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="sessions",
            title="Sessions",
            description="All agent sessions.",
            create_label=None,
            create_url=None,
            rows=_build_session_rows(sessions, project_lookup, agent_lookup),
            empty_message="No sessions yet.",
        )
        return _render_template(
            request,
            "sessions.html",
            {
                "title": "Sessions",
                "active_nav": "sessions",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/sessions/{session_id}")
    def session_edit_ui(session_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        session = database.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        project = database.get_project(session.project_id)
        agent = database.get_agent(session.agent_id)
        project_name = project.name if project else session.project_id
        vm = database.get_vm_target(agent.vm_target_id) if agent and agent.vm_target_id else None
        vm_label = f"{project_name} -> {vm.name}" if vm else "No VM"
        agent_name = agent.name if agent else session.agent_id
        return _render_template(
            request,
            "session_edit.html",
            {
                "title": "Session",
                "active_nav": "sessions",
                "growl_message": None,
                "session": session,
                "project_name": project_name,
                "vm_label": vm_label,
                "agent_name": agent_name,
            },
        )

    @app.post("/sessions/{session_id}/edit")
    async def update_session(session_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        status = str(form.get("status", "")).strip()
        if not status:
            raise HTTPException(status_code=400, detail="Status is required")
        database.update_session(session_id, status=status)
        return RedirectResponse(f"/ui/sessions/{session_id}?saved=session_updated", status_code=303)

    @app.post("/sessions/{session_id}/delete")
    async def delete_session(session_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_session(session_id)
        return RedirectResponse("/ui/sessions?saved=session_deleted", status_code=303)

    @app.get("/logs/tail")
    def tail_logs(limit: int = Query(default=100, ge=1, le=1000), user: str = Depends(_require_login)) -> dict[str, Any]:
        return {"entries": [], "limit": limit}

    @app.get("/")
    def home(request: Request) -> Response:
        if not database.list_users():
            return RedirectResponse("/setup")
        if not request.session.get("user"):
            return RedirectResponse("/login")
        return RedirectResponse("/ui")

    @app.get("/.well-known/appspecific/com.chrome.devtools.json")
    def chrome_devtools_marker() -> Response:
        return Response(status_code=204)

    @app.get("/setup")
    def setup_page(request: Request) -> Response:
        already_configured = bool(database.list_users())
        return _render_template(
            request,
            "setup.html",
            {
                "title": "Setup",
                "active_nav": "",
                "growl_message": None,
                "already_configured": already_configured,
            },
        )

    @app.post("/setup")
    async def setup(request: Request) -> RedirectResponse:
        if database.list_users():
            return RedirectResponse("/login", status_code=303)
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        if not username or not password:
            raise HTTPException(status_code=400, detail="Missing username or password")
        if database.get_user(username):
            raise HTTPException(status_code=409, detail="User exists")
        salt = secrets.token_bytes(16)
        password_hash = _hash_password(password, salt)
        database.insert_user(
            user_id=str(uuid.uuid4()),
            username=username,
            password_hash=password_hash,
            salt=base64.b64encode(salt).decode("ascii"),
        )
        return RedirectResponse("/login", status_code=303)

    @app.get("/login")
    def login_page(request: Request) -> Response:
        return _render_template(
            request,
            "login.html",
            {
                "title": "Login",
                "active_nav": "",
                "growl_message": None,
            },
        )

    @app.post("/login")
    async def login(request: Request) -> RedirectResponse:
        form = await request.form()
        username = str(form.get("username", "")).strip()
        password = str(form.get("password", ""))
        user = database.get_user(username)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid credentials")
        if not _verify_password(password, user.salt, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        request.session["user"] = username
        return RedirectResponse("/ui", status_code=303)

    @app.get("/logout")
    def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.get("/ui")
    def ui(request: Request, user: str = Depends(_require_login)) -> Response:
        growl_message = _growl_message(request.query_params.get("saved"))
        # Find current active sprint
        sprints = database.list_sprints()
        current_sprint = None
        for sprint in sprints:
            if sprint.status == "active":
                current_sprint = sprint
                break
        # Get tickets in current sprint, sorted by priority (high first)
        tickets: list[Any] = []
        if current_sprint:
            tickets = database.list_tickets_in_sprint(current_sprint.id)
            priority_order = {"high": 0, "medium": 1, "low": 2}
            tickets.sort(key=lambda t: priority_order.get(t.priority, 3))
        # Build project lookup for display
        projects = database.list_projects()
        project_lookup = {p.id: p.name for p in projects}
        return _render_template(
            request,
            "admin.html",
            {
                "title": "Home",
                "active_nav": "home",
                "growl_message": growl_message,
                "user": user,
                "current_sprint": current_sprint,
                "tickets": tickets,
                "project_lookup": project_lookup,
            },
        )

    @app.get("/ui/status")
    def status_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        status = database.get_supervisor_state()
        work_items = database.fetch_ready_work_items(utc_now())
        failed_work_items = database.list_work_items(status="failed")
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "status.html",
            {
                "title": "Status",
                "active_nav": "status",
                "growl_message": growl_message,
                "user": user,
                "status": status,
                "work_items": work_items,
                "failed_work_items": failed_work_items,
            },
        )

    @app.get("/ui/slack")
    def slack_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        slack_source = database.get_task_source("slack")
        slack_config = slack_source.config if slack_source else {}
        slack_channels = ", ".join(slack_config.get("channels", []))
        slack_bot = database.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        slack_app = database.get_credential_by_name(SLACK_PROVIDER, SLACK_APP_TOKEN_NAME)
        slack_admin = database.get_credential_by_name(SLACK_PROVIDER, "admin_user_id")
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "slack.html",
            {
                "title": "Slack",
                "active_nav": "slack",
                "growl_message": growl_message,
                "user": user,
                "slack_source": slack_source,
                "slack_channels": slack_channels,
                "slack_bot": slack_bot,
                "slack_app": slack_app,
                "slack_admin": slack_admin,
            },
        )

    @app.get("/ui/standup")
    def standup_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        standup_source = database.get_task_source(StandupSource.id)
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "standup.html",
            {
                "title": "Standup",
                "active_nav": "standup",
                "growl_message": growl_message,
                "standup_source": standup_source,
            },
        )

    @app.get("/ui/work-items")
    def work_items_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        status_filter = request.query_params.get("status")
        work_items = database.list_work_items(status=status_filter) if status_filter else database.list_work_items()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="work_items",
            title="Work Items",
            description="All work items across sources.",
            create_label=None,
            create_url=None,
            rows=_build_work_item_rows(work_items),
            empty_message="No work items yet.",
        )
        return _render_template(
            request,
            "work_items.html",
            {
                "title": "Work Items",
                "active_nav": "work_items",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/work-items/{work_id:path}")
    def work_item_edit_ui(work_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        item = database.get_work_item(work_id)
        if not item:
            raise HTTPException(status_code=404, detail="Work item not found")
        checkpoint = json.dumps(item.checkpoint, indent=2, sort_keys=True)
        return _render_template(
            request,
            "work_item_edit.html",
            {
                "title": "Work Item",
                "active_nav": "work_items",
                "growl_message": None,
                "item": item,
                "checkpoint": checkpoint,
            },
        )

    @app.get("/ui/projects")
    def projects_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="projects",
            title="Projects",
            description="All configured projects and defaults.",
            create_label="Create Project",
            create_url="/ui/projects/create?return_to=/ui/projects",
            rows=_build_project_rows(projects, database),
            empty_message="No projects yet.",
        )
        return _render_template(
            request,
            "projects.html",
            {
                "title": "Projects",
                "active_nav": "projects",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/projects/create")
    def projects_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/projects")
        if not return_to.startswith("/ui"):
            return_to = "/ui/projects"
        remote_tokens = database.list_remote_tokens()
        agents = database.list_agents()
        mirror_repo_path_base = os.environ.get("WINTERMUTE_MIRROR_REPO_PATH_BASE", "/home/user/git")
        return _render_template(
            request,
            "project_create.html",
            {
                "title": "Create Project",
                "active_nav": "projects",
                "growl_message": None,
                "return_to": return_to,
                "default_prompt_template": DEFAULT_PROJECT_PROMPT_TEMPLATE,
                "remote_tokens": remote_tokens,
                "agents": agents,
                "mirror_repo_path_base": mirror_repo_path_base,
            },
        )

    @app.get("/ui/vms")
    def vms_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        vm_targets = database.list_vm_targets()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="vms",
            title="VM Targets",
            description="Compute targets available for sessions.",
            create_label="Add VM",
            create_url="/ui/vms/create?return_to=/ui/vms",
            rows=_build_vm_rows(vm_targets),
            empty_message="No VM targets yet.",
        )
        return _render_template(
            request,
            "vms.html",
            {
                "title": "VM Targets",
                "active_nav": "vms",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/vms/create")
    def vms_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/vms")
        if not return_to.startswith("/ui"):
            return_to = "/ui/vms"
        return _render_template(
            request,
            "vm_create.html",
            {
                "title": "Add VM",
                "active_nav": "vms",
                "growl_message": None,
                "return_to": return_to,
            },
        )

    @app.get("/ui/agents")
    def agents_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        agents = database.list_agents()
        vm_targets = database.list_vm_targets()
        vm_lookup = {vm.id: vm.name for vm in vm_targets}
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="agents",
            title="Agents",
            description="Agent profiles and execution settings.",
            create_label="Add Agent",
            create_url="/ui/agents/create?return_to=/ui/agents",
            rows=_build_agent_rows(agents, vm_lookup),
            empty_message="No agents yet.",
        )
        return _render_template(
            request,
            "agents.html",
            {
                "title": "Agents",
                "active_nav": "agents",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/agents/create")
    def agents_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/agents")
        if not return_to.startswith("/ui"):
            return_to = "/ui/agents"
        vm_targets = database.list_vm_targets()

        # Parse error from query params (e.g., "field:message" or just "message")
        error_param = request.query_params.get("error")
        error_field = None
        error_message = None
        if error_param:
            if ":" in error_param:
                error_field, error_message = error_param.split(":", 1)
            else:
                error_message = error_param

        return _render_template(
            request,
            "agent_create.html",
            {
                "title": "Add Agent",
                "active_nav": "agents",
                "growl_message": None,
                "return_to": return_to,
                "vm_targets": vm_targets,
                "error_field": error_field,
                "error_message": error_message,
            },
        )

    @app.get("/ui/agent-responses")
    def agent_responses_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        responses = database.list_agent_responses()
        growl_message = _growl_message(request.query_params.get("saved"))
        agents = database.list_agents()
        agent_lookup = {agent.id: agent.name for agent in agents}
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="agent_responses",
            title="Agent Responses",
            description="Automatic response rules for agent patterns.",
            create_label="Add Response Rule",
            create_url="/ui/agent-responses/create?return_to=/ui/agent-responses",
            rows=_build_agent_response_rows(responses, agent_lookup),
            empty_message="No response rules yet.",
        )
        return _render_template(
            request,
            "agent_responses.html",
            {
                "title": "Agent Responses",
                "active_nav": "agent_responses",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/agent-responses/create")
    def agent_response_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/agent-responses")
        if not return_to.startswith("/ui"):
            return_to = "/ui/agent-responses"
        agent_id = request.query_params.get("agent_id")
        agents = database.list_agents()
        return _render_template(
            request,
            "agent_response_create.html",
            {
                "title": "Add Response Rule",
                "active_nav": "agent_responses",
                "growl_message": None,
                "agents": agents,
                "agent_id": agent_id,
                "return_to": return_to,
            },
        )

    @app.get("/ui/agent-responses/{response_id}/edit")
    def agent_response_edit_ui(response_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        response = database.get_agent_response(response_id)
        if not response:
            raise HTTPException(status_code=404, detail="Response rule not found")
        return_to = request.query_params.get("return_to", "/ui/agent-responses")
        if not return_to.startswith("/ui"):
            return_to = "/ui/agent-responses"
        agents = database.list_agents()
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "agent_response_edit.html",
            {
                "title": "Edit Response Rule",
                "active_nav": "agent_responses",
                "growl_message": growl_message,
                "response": response,
                "agents": agents,
                "return_to": return_to,
            },
        )

    # --- Sprints UI ---

    @app.get("/ui/sprints")
    def sprints_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        sprints = database.list_sprints()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="sprints",
            title="Sprints",
            description="Sprint cycles for project management.",
            create_label="Add Sprint",
            create_url="/ui/sprints/create?return_to=/ui/sprints",
            rows=_build_sprint_rows(sprints),
            empty_message="No sprints yet.",
        )
        return _render_template(
            request,
            "sprints.html",
            {
                "title": "Sprints",
                "active_nav": "sprints",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/sprints/create")
    def sprint_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/sprints")
        if not return_to.startswith("/ui"):
            return_to = "/ui/sprints"
        # Default dates: today and today+14
        from datetime import date, timedelta
        today = date.today()
        default_start = today.isoformat()
        default_end = (today + timedelta(days=14)).isoformat()
        return _render_template(
            request,
            "sprint_create.html",
            {
                "title": "Add Sprint",
                "active_nav": "sprints",
                "growl_message": None,
                "return_to": return_to,
                "default_start": default_start,
                "default_end": default_end,
            },
        )

    @app.get("/ui/sprints/{sprint_id}/edit")
    def sprint_edit_ui(sprint_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        sprint = database.get_sprint(sprint_id)
        if not sprint:
            raise HTTPException(status_code=404, detail="Sprint not found")
        return_to = request.query_params.get("return_to", "/ui/sprints")
        if not return_to.startswith("/ui"):
            return_to = "/ui/sprints"
        tickets = database.list_tickets_in_sprint(sprint_id)
        return _render_template(
            request,
            "sprint_edit.html",
            {
                "title": f"Edit Sprint: {sprint.name}",
                "active_nav": "sprints",
                "growl_message": _growl_message(request.query_params.get("saved")),
                "sprint": sprint,
                "tickets": tickets,
                "return_to": return_to,
            },
        )

    @app.post("/sprints")
    async def create_sprint(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        start_date = str(form.get("start_date", "")).strip()
        end_date = str(form.get("end_date", "")).strip()
        enabled = form.get("enabled") == "on"
        return_to = str(form.get("return_to", "/ui/sprints"))
        if not return_to.startswith("/ui"):
            return_to = "/ui/sprints"
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        if not start_date or not end_date:
            raise HTTPException(status_code=400, detail="Start and end dates are required")
        sprint_id = str(uuid.uuid4())
        database.insert_sprint(
            sprint_id=sprint_id,
            name=name,
            start_date=start_date,
            end_date=end_date,
            enabled=enabled,
            status="active",
        )
        return RedirectResponse(f"{return_to}?saved=sprint", status_code=303)

    @app.post("/sprints/{sprint_id}/edit")
    async def update_sprint(sprint_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        start_date = str(form.get("start_date", "")).strip()
        end_date = str(form.get("end_date", "")).strip()
        enabled = form.get("enabled") == "on"
        status = str(form.get("status", "active")).strip()
        database.update_sprint(
            sprint_id,
            name=name if name else None,
            start_date=start_date if start_date else None,
            end_date=end_date if end_date else None,
            enabled=enabled,
            status=status if status else None,
        )
        return RedirectResponse(f"/ui/sprints/{sprint_id}/edit?saved=sprint", status_code=303)

    @app.post("/sprints/{sprint_id}/delete")
    async def delete_sprint(sprint_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_sprint(sprint_id)
        return RedirectResponse("/ui/sprints?saved=sprint_deleted", status_code=303)

    # Legacy project-vms routes - VMs are now linked directly to agents
    @app.get("/ui/project-vms")
    def project_vms_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/agents", status_code=302)

    @app.get("/ui/project-vms/create")
    def project_vms_create_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/agents", status_code=302)

    # Legacy routes - redirect to unified issue sources
    @app.get("/ui/github-sources")
    def github_sources_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/issue-sources", status_code=302)

    @app.get("/ui/gitlab-sources")
    def gitlab_sources_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/issue-sources", status_code=302)

    # Legacy token routes - redirect to unified remote tokens
    @app.get("/ui/github-tokens")
    def github_tokens_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/remote-tokens", status_code=302)

    @app.get("/ui/github-tokens/create")
    def github_tokens_create_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/remote-tokens/create", status_code=302)

    @app.get("/ui/github-tokens/{token_id}/edit")
    def github_tokens_edit_ui_redirect(token_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse(f"/ui/remote-tokens/{token_id}/edit", status_code=302)

    @app.get("/ui/gitlab-tokens")
    def gitlab_tokens_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/remote-tokens", status_code=302)

    @app.get("/ui/gitlab-tokens/create")
    def gitlab_tokens_create_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/remote-tokens/create", status_code=302)

    @app.get("/ui/gitlab-tokens/{token_id}/edit")
    def gitlab_tokens_edit_ui_redirect(token_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse(f"/ui/remote-tokens/{token_id}/edit", status_code=302)

    # --- Unified Remote Tokens UI ---

    @app.get("/ui/remote-tokens")
    def remote_tokens_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        tokens = database.list_remote_tokens()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="remote_tokens",
            title="Remote Tokens",
            description="API tokens for GitHub, GitLab, and other providers.",
            create_label="Add Remote Token",
            create_url="/ui/remote-tokens/create?return_to=/ui/remote-tokens",
            rows=_build_remote_token_rows(tokens),
            empty_message="No remote tokens yet.",
        )
        return _render_template(
            request,
            "remote_tokens.html",
            {
                "title": "Remote Tokens",
                "active_nav": "remote_tokens",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/remote-tokens/create")
    def remote_tokens_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/remote-tokens")
        if not return_to.startswith("/ui"):
            return_to = "/ui/remote-tokens"
        return _render_template(
            request,
            "remote_token_create.html",
            {
                "title": "Add Remote Token",
                "active_nav": "remote_tokens",
                "growl_message": None,
                "return_to": return_to,
            },
        )

    @app.get("/ui/remote-tokens/{token_id}/edit")
    def remote_tokens_edit_ui(token_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        token = database.get_remote_token(token_id)
        if not token:
            raise HTTPException(status_code=404, detail="Remote token not found")
        return _render_template(
            request,
            "remote_token_edit.html",
            {
                "title": "Edit Remote Token",
                "active_nav": "remote_tokens",
                "growl_message": None,
                "token": token,
            },
        )

    @app.get("/ui/github-sources/create")
    def github_sources_create_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/issue-sources/create", status_code=302)

    @app.get("/ui/gitlab-sources/create")
    def gitlab_sources_create_ui_redirect(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/issue-sources/create", status_code=302)

    @app.get("/ui/github-sources/{source_id}/edit")
    def github_sources_edit_ui_redirect(source_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse(f"/ui/issue-sources/{source_id}/edit", status_code=302)

    @app.get("/ui/gitlab-sources/{source_id}/edit")
    def gitlab_sources_edit_ui_redirect(source_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse(f"/ui/issue-sources/{source_id}/edit", status_code=302)

    # --- Unified Issue Sources UI ---

    @app.get("/ui/issue-sources")
    def issue_sources_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        sources = database.list_issue_sources()
        project_lookup = {project.id: project.name for project in database.list_projects()}
        github_token_lookup = {token.id: token.note or token.user_login or token.id for token in database.list_github_tokens()}
        gitlab_token_lookup = {token.id: token.note or token.user_login or token.id for token in database.list_gitlab_tokens()}
        token_lookup = {**github_token_lookup, **gitlab_token_lookup}
        agent_lookup = {agent.id: agent.name for agent in database.list_agents()}
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="issue_sources",
            title="Issue Sources",
            description="GitHub and GitLab issue sources for ticket polling.",
            create_label="Add Issue Source",
            create_url="/ui/issue-sources/create?return_to=/ui/issue-sources",
            rows=_build_issue_source_rows(sources, project_lookup, token_lookup, agent_lookup),
            empty_message="No issue sources yet.",
        )
        return _render_template(
            request,
            "issue_sources.html",
            {
                "title": "Issue Sources",
                "active_nav": "issue_sources",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/issue-sources/create")
    def issue_sources_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        github_tokens = database.list_github_tokens()
        gitlab_tokens = database.list_gitlab_tokens()
        agents = database.list_agents()
        return_to = request.query_params.get("return_to", "/ui/issue-sources")
        if not return_to.startswith("/ui"):
            return_to = "/ui/issue-sources"
        token_notice = ""
        if not github_tokens and not gitlab_tokens:
            token_notice = "Add a GitHub or GitLab token before creating a source."
        return _render_template(
            request,
            "issue_source_create.html",
            {
                "title": "Add Issue Source",
                "active_nav": "issue_sources",
                "growl_message": None,
                "projects": projects,
                "github_tokens": github_tokens,
                "gitlab_tokens": gitlab_tokens,
                "agents": agents,
                "return_to": return_to,
                "token_notice": token_notice,
            },
        )

    @app.get("/ui/issue-sources/{source_id}/edit")
    def issue_sources_edit_ui(source_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        source = database.get_issue_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Issue source not found")
        projects = database.list_projects()
        github_tokens = database.list_github_tokens()
        gitlab_tokens = database.list_gitlab_tokens()
        agents = database.list_agents()
        labels = ", ".join(source.labels)
        return _render_template(
            request,
            "issue_source_edit.html",
            {
                "title": "Edit Issue Source",
                "active_nav": "issue_sources",
                "growl_message": None,
                "source": source,
                "projects": projects,
                "github_tokens": github_tokens,
                "gitlab_tokens": gitlab_tokens,
                "agents": agents,
                "labels": labels,
            },
        )

    @app.post("/issue-sources")
    async def create_issue_source(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        provider = str(form.get("provider", "github")).strip()
        project_id = str(form.get("project_id", "")).strip()
        if provider == "github":
            token_id = str(form.get("github_token_id", "")).strip()
        else:
            token_id = str(form.get("gitlab_token_id", "")).strip()
        agent_id = str(form.get("agent_id", "")).strip() or None
        repo = str(form.get("repo", "")).strip()
        state = str(form.get("state", "open")).strip() or "open"
        labels_raw = str(form.get("labels", "")).strip()
        labels = _parse_labels(labels_raw)
        enabled = form.get("enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        poll_interval_raw = str(form.get("poll_interval_seconds", "60")).strip()
        poll_interval_seconds = max(10, int(poll_interval_raw)) if poll_interval_raw.isdigit() else 60
        if not project_id or not token_id or not repo:
            raise HTTPException(status_code=400, detail="Missing issue source fields")
        if provider == "github":
            if not database.get_github_token(token_id):
                raise HTTPException(status_code=400, detail="GitHub token not found")
        else:
            if not database.get_gitlab_token(token_id):
                raise HTTPException(status_code=400, detail="GitLab token not found")
        if agent_id and not database.get_agent(agent_id):
            raise HTTPException(status_code=400, detail="Agent not found")
        if auto_start and not agent_id:
            raise HTTPException(status_code=400, detail="Agent is required for auto-start")
        database.insert_issue_source(
            str(uuid.uuid4()),
            provider=provider,
            token_id=token_id,
            agent_id=agent_id,
            project_id=project_id,
            repo=repo,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
            poll_interval_seconds=poll_interval_seconds,
        )
        return_to = str(form.get("return_to", "/ui/issue-sources")).strip() or "/ui/issue-sources"
        if not return_to.startswith("/ui"):
            return_to = "/ui/issue-sources"
        return RedirectResponse(f"{return_to}?saved=issue_source_created", status_code=303)

    @app.post("/issue-sources/{source_id}/edit")
    async def update_issue_source(source_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        provider = str(form.get("provider", "github")).strip()
        project_id = str(form.get("project_id", "")).strip()
        if provider == "github":
            token_id = str(form.get("github_token_id", "")).strip()
        else:
            token_id = str(form.get("gitlab_token_id", "")).strip()
        agent_id = str(form.get("agent_id", "")).strip() or None
        repo = str(form.get("repo", "")).strip()
        state = str(form.get("state", "open")).strip() or "open"
        labels_raw = str(form.get("labels", "")).strip()
        labels = _parse_labels(labels_raw)
        enabled = form.get("enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        poll_interval_raw = str(form.get("poll_interval_seconds", "60")).strip()
        poll_interval_seconds = max(10, int(poll_interval_raw)) if poll_interval_raw.isdigit() else 60
        if not project_id or not token_id or not repo:
            raise HTTPException(status_code=400, detail="Missing issue source fields")
        if agent_id and not database.get_agent(agent_id):
            raise HTTPException(status_code=400, detail="Agent not found")
        if auto_start and not agent_id:
            raise HTTPException(status_code=400, detail="Agent is required for auto-start")
        database.update_issue_source(
            source_id,
            provider=provider,
            token_id=token_id,
            agent_id=agent_id,
            project_id=project_id,
            repo=repo,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
            poll_interval_seconds=poll_interval_seconds,
        )
        return RedirectResponse("/ui/issue-sources?saved=issue_source_updated", status_code=303)

    @app.post("/issue-sources/{source_id}/delete")
    async def delete_issue_source(source_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_issue_source(source_id)
        return RedirectResponse("/ui/issue-sources?saved=issue_source_deleted", status_code=303)

    @app.get("/ui/project-vms/{mapping_id}/edit")
    def project_vms_edit_ui_redirect(mapping_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/agents", status_code=302)

    @app.post("/project_vms/{mapping_id}/edit")
    async def project_vms_update_legacy(mapping_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        return RedirectResponse("/ui/agents?saved=project_vms_deprecated", status_code=303)

    @app.get("/ui/tickets")
    def tickets_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        tickets = database.list_tickets()
        projects = database.list_projects()
        agents = database.list_agents()
        users = database.list_users()
        project_lookup = {project.id: project.name for project in projects}
        agent_lookup = {agent.id: agent.name for agent in agents}
        user_lookup = {u.id: u.username for u in users}
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="tickets",
            title="Tickets",
            description=None,
            create_label="Create Ticket",
            create_url="/ui/tickets/create?return_to=/ui/tickets",
            rows=_build_ticket_rows(tickets, project_lookup, agent_lookup, user_lookup),
            empty_message="No tickets yet.",
        )
        return _render_template(
            request,
            "tickets.html",
            {
                "title": "Tickets",
                "active_nav": "tickets",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/tickets/create")
    def tickets_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        agents = database.list_agents()
        sprints = database.list_sprints(status="active")
        users = database.list_users()
        return_to = request.query_params.get("return_to", "/ui/tickets")
        if not return_to.startswith("/ui"):
            return_to = "/ui/tickets"
        selected_project_id = request.query_params.get("project_id", "")
        return _render_template(
            request,
            "ticket_create.html",
            {
                "title": "Create Ticket",
                "active_nav": "tickets",
                "growl_message": None,
                "projects": projects,
                "agents": agents,
                "sprints": sprints,
                "users": users,
                "return_to": return_to,
                "selected_project_id": selected_project_id,
            },
        )

    @app.post("/ui/column-preferences")
    async def update_column_preferences(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        model = str(form.get("model", "")).strip()
        config = LIST_TABLE_CONFIGS.get(model)
        if not config:
            raise HTTPException(status_code=400, detail="Unknown model for column preferences")
        return_to = str(form.get("return_to", f"/ui/{model}")).strip() or f"/ui/{model}"
        if not return_to.startswith("/ui"):
            return_to = f"/ui/{model}"
        columns_raw = str(form.get("columns", "")).strip()
        columns: list[str] = []
        if columns_raw:
            try:
                parsed = json.loads(columns_raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                columns = [str(item).strip() for item in parsed if str(item).strip()]
            elif isinstance(parsed, str):
                columns = [item.strip() for item in parsed.split(",") if item.strip()]
        available_keys = [column["key"] for column in config["columns"]]
        columns = [key for key in columns if key in available_keys]
        if not columns:
            columns = [key for key in config["default"] if key in available_keys]
        user_record = database.get_user(user)
        if not user_record:
            raise HTTPException(status_code=400, detail="User not found")
        database.upsert_column_preferences(user_record.id, model, columns)
        separator = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{return_to}{separator}saved=columns_updated", status_code=303)

    # -------------------------------------------------------------------------
    # Session File Configs
    # -------------------------------------------------------------------------

    def _build_session_file_config_rows(configs: list,) -> list[dict[str, Any]]:
        rows = []
        for config in configs:
            rows.append({
                "id": config.id,
                "cells": {
                    "id": {
                        "text": config.id[:8],
                        "href": f"/ui/session-file-configs/{config.id}/edit"
                    },
                    "name": {
                        "text": config.name,
                        "href": f"/ui/session-file-configs/{config.id}/edit"
                    },
                    "description": {
                        "text": config.description or "-"
                    },
                    "created_at": {
                        "text": config.created_at[:16].replace("T", " ") if config.created_at else "-"
                    },
                    "updated_at": {
                        "text": config.updated_at[:16].replace("T", " ") if config.updated_at else "-"
                    },
                },
            })
        return rows

    @app.get("/ui/session-file-configs")
    def session_file_configs_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        configs = database.list_session_file_configs()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="session_file_configs",
            title="Session File Configs",
            description="Define file sets for agent memory persistence.",
            create_label="Add Config",
            create_url="/ui/session-file-configs/create",
            rows=_build_session_file_config_rows(configs),
            empty_message="No session file configs yet.",
        )
        return _render_template(
            request,
            "session_file_configs.html",
            {
                "title": "Session File Configs",
                "active_nav": "session_file_configs",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/session-file-configs/create")
    def session_file_configs_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/session-file-configs")
        if not return_to.startswith("/ui"):
            return_to = "/ui/session-file-configs"
        return _render_template(
            request,
            "session_file_config_create.html",
            {
                "title": "Create Session File Config",
                "active_nav": "session_file_configs",
                "growl_message": None,
                "return_to": return_to,
            },
        )

    @app.get("/ui/session-file-configs/{config_id}/edit")
    def session_file_configs_edit_ui(config_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        config = database.get_session_file_config(config_id)
        if not config:
            raise HTTPException(status_code=404, detail="Config not found")
        definitions = database.list_session_file_definitions(config_id)
        return _render_template(
            request,
            "session_file_config_edit.html",
            {
                "title": "Edit Session File Config",
                "active_nav": "session_file_configs",
                "growl_message": _growl_message(request.query_params.get("saved")),
                "config": config,
                "definitions": definitions,
            },
        )

    @app.post("/session-file-configs")
    async def create_session_file_config(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        description = str(form.get("description", "")).strip() or None
        return_to = str(form.get("return_to", "/ui/session-file-configs")).strip()
        if not return_to.startswith("/ui"):
            return_to = "/ui/session-file-configs"
        if not name:
            raise HTTPException(status_code=400, detail="Name is required")
        config_id = str(uuid.uuid4())
        database.insert_session_file_config(config_id, name, description)
        return RedirectResponse(f"/ui/session-file-configs/{config_id}/edit?saved=config_created", status_code=303)

    @app.post("/session-file-configs/{config_id}/edit")
    async def update_session_file_config(config_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip() or None
        description = str(form.get("description", "")).strip() or None
        database.update_session_file_config(config_id, name=name, description=description)
        return RedirectResponse(f"/ui/session-file-configs/{config_id}/edit?saved=config_updated", status_code=303)

    @app.post("/session-file-configs/{config_id}/delete")
    async def delete_session_file_config(config_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_session_file_config(config_id)
        return RedirectResponse("/ui/session-file-configs?saved=config_deleted", status_code=303)

    @app.post("/ui/session_file_configs/bulk-action")
    async def session_file_configs_bulk_action(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        """Handle bulk actions on session file configs (e.g., clone)."""
        form = await request.form()
        action = str(form.get("action", "")).strip()
        ids = form.getlist("ids")
        if not action or not ids:
            raise HTTPException(status_code=400, detail="Missing action or ids")

        cloned_count = 0
        if action == "clone":
            # Get all existing configs to check for unique name conflicts
            existing_configs = database.list_session_file_configs()
            existing_names = {c.name for c in existing_configs}

            for config_id in ids:
                config = database.get_session_file_config(config_id)
                if not config:
                    continue

                # Generate unique name
                new_name = _generate_unique_string(config.name, existing_names)
                existing_names.add(new_name)

                # Create the clone with new UUID
                new_config_id = str(uuid.uuid4())
                database.insert_session_file_config(
                    config_id=new_config_id,
                    name=new_name,
                    description=config.description,
                )

                # Clone all file definitions
                definitions = database.list_session_file_definitions(config_id)
                for defn in definitions:
                    database.insert_session_file_definition(
                        definition_id=str(uuid.uuid4()),
                        config_id=new_config_id,
                        filename=defn.filename,
                        default_content=defn.default_content,
                        description=defn.description,
                        required=defn.required,
                        sync_on_exit=defn.sync_on_exit,
                        sort_order=defn.sort_order,
                    )

                cloned_count += 1

        return RedirectResponse(f"/ui/session-file-configs?saved=cloned_{cloned_count}", status_code=303)

    # -------------------------------------------------------------------------
    # Session File Definitions
    # -------------------------------------------------------------------------

    @app.get("/ui/session-file-definitions/{definition_id}/edit")
    def session_file_definitions_edit_ui(definition_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        definition = database.get_session_file_definition(definition_id)
        if not definition:
            raise HTTPException(status_code=404, detail="Definition not found")
        return _render_template(
            request,
            "session_file_definition_edit.html",
            {
                "title": "Edit File Definition",
                "active_nav": "session_file_configs",
                "growl_message": _growl_message(request.query_params.get("saved")),
                "definition": definition,
            },
        )

    @app.post("/session-file-definitions")
    async def create_session_file_definition(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        config_id = str(form.get("config_id", "")).strip()
        filename = str(form.get("filename", "")).strip()
        description = str(form.get("description", "")).strip() or None
        default_content = str(form.get("default_content", ""))
        required = "required" in form
        sync_on_exit = "sync_on_exit" in form
        sort_order = int(form.get("sort_order", 0) or 0)
        if not config_id or not filename:
            raise HTTPException(status_code=400, detail="Missing required fields")
        definition_id = str(uuid.uuid4())
        database.insert_session_file_definition(
            definition_id, config_id, filename, default_content, description=description, required=required, sync_on_exit=sync_on_exit, sort_order=sort_order
        )
        return RedirectResponse(f"/ui/session-file-configs/{config_id}/edit?saved=definition_created", status_code=303)

    @app.post("/session-file-definitions/{definition_id}/edit")
    async def update_session_file_definition(definition_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        config_id = str(form.get("config_id", "")).strip()
        filename = str(form.get("filename", "")).strip() or None
        description = str(form.get("description", "")).strip() or None
        default_content = str(form.get("default_content", ""))
        required = "required" in form
        sync_on_exit = "sync_on_exit" in form
        sort_order = int(form.get("sort_order", 0) or 0)
        database.update_session_file_definition(
            definition_id,
            filename=filename,
            description=description,
            default_content=default_content,
            required=required,
            sync_on_exit=sync_on_exit,
            sort_order=sort_order,
        )
        return RedirectResponse(f"/ui/session-file-configs/{config_id}/edit?saved=definition_updated", status_code=303)

    @app.post("/session-file-definitions/{definition_id}/delete")
    async def delete_session_file_definition(definition_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        config_id = str(form.get("config_id", "")).strip()
        database.delete_session_file_definition(definition_id)
        if config_id:
            return RedirectResponse(f"/ui/session-file-configs/{config_id}/edit?saved=definition_deleted", status_code=303)
        return RedirectResponse("/ui/session-file-configs?saved=definition_deleted", status_code=303)

    # -------------------------------------------------------------------------
    # Session Files (per-agent file content)
    # -------------------------------------------------------------------------

    @app.get("/ui/session-files/{file_id}/edit")
    def edit_session_file_ui(file_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        file = database.get_session_file(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="Session file not found")
        agent = database.get_agent(file.agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        definition = database.get_session_file_definition(file.definition_id)
        if not definition:
            raise HTTPException(status_code=404, detail="Definition not found")
        return _render_template(
            request,
            "session_file_edit.html",
            {
                "title": f"Edit {definition.filename}",
                "active_nav": "agents",
                "growl_message": None,
                "file": file,
                "agent": agent,
                "definition": definition,
            },
        )

    @app.post("/session-files/{file_id}/edit")
    async def update_session_file(file_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        content = str(form.get("content", ""))
        agent_id = str(form.get("agent_id", "")).strip()
        file = database.get_session_file(file_id)
        if not file:
            raise HTTPException(status_code=404, detail="Session file not found")
        database.update_session_file(file_id, content=content)
        if agent_id:
            return RedirectResponse(f"/ui/session-files/{file_id}/edit?saved=file_updated", status_code=303)
        return RedirectResponse(f"/ui/session-files/{file_id}/edit?saved=file_updated", status_code=303)

    @app.post("/session-files/{file_id}/delete")
    async def delete_session_file(file_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        agent_id = str(form.get("agent_id", "")).strip()
        database.delete_session_file(file_id)
        if agent_id:
            return RedirectResponse(f"/ui/agents/{agent_id}/edit?saved=file_deleted", status_code=303)
        return RedirectResponse("/ui/agents?saved=file_deleted", status_code=303)

    # -------------------------------------------------------------------------
    # Metric Definitions UI
    # -------------------------------------------------------------------------

    @app.get("/ui/metric-definitions")
    def metric_definitions_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        definitions = database.list_metric_definitions()
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="metric_definitions",
            title="Metric Definitions",
            description="Configure what metrics to collect for agents",
            create_label="Create Metric Definition",
            create_url="/ui/metric-definitions/create",
            rows=_build_metric_definition_rows(definitions),
            empty_message="No metric definitions yet.",
        )
        return _render_template(
            request,
            "metric_definitions.html",
            {
                "title": "Metric Definitions",
                "active_nav": "metric_definitions",
                "growl_message": growl_message,
                **table_context,
            },
        )

    @app.get("/ui/metric-definitions/create")
    def metric_definition_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return _render_template(
            request,
            "metric_definition_edit.html",
            {
                "title": "Create Metric Definition",
                "active_nav": "metric_definitions",
                "growl_message": None,
                "definition": None,
            },
        )

    @app.post("/metric-definitions")
    async def create_metric_definition(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        metric_type = str(form.get("metric_type", "")).strip().upper()
        frequency_str = str(form.get("recording_frequency_minutes", "5")).strip()
        enabled = form.get("enabled") == "on"
        try:
            frequency = int(frequency_str) if frequency_str else 5
        except ValueError:
            frequency = 5
        definition_id = str(uuid.uuid4())
        database.insert_metric_definition(
            definition_id=definition_id,
            metric_type=metric_type,
            recording_frequency_minutes=frequency,
            enabled=enabled,
        )
        return RedirectResponse("/ui/metric-definitions?saved=definition_created", status_code=303)

    @app.get("/ui/metric-definitions/{definition_id}/edit")
    def metric_definition_edit_ui(definition_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        definition = database.get_metric_definition(definition_id)
        if not definition:
            raise HTTPException(status_code=404, detail="Metric definition not found")
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "metric_definition_edit.html",
            {
                "title": "Edit Metric Definition",
                "active_nav": "metric_definitions",
                "growl_message": growl_message,
                "definition": definition,
            },
        )

    @app.post("/metric-definitions/{definition_id}/edit")
    async def update_metric_definition(definition_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        metric_type = str(form.get("metric_type", "")).strip().upper()
        frequency_str = str(form.get("recording_frequency_minutes", "5")).strip()
        enabled = form.get("enabled") == "on"
        try:
            frequency = int(frequency_str) if frequency_str else 5
        except ValueError:
            frequency = 5
        database.update_metric_definition(
            definition_id,
            metric_type=metric_type,
            recording_frequency_minutes=frequency,
            enabled=enabled,
        )
        return RedirectResponse(f"/ui/metric-definitions/{definition_id}/edit?saved=definition_updated", status_code=303)

    @app.post("/metric-definitions/{definition_id}/delete")
    def delete_metric_definition(definition_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_metric_definition(definition_id)
        return RedirectResponse("/ui/metric-definitions?saved=definition_deleted", status_code=303)

    # -------------------------------------------------------------------------
    # Agent Metrics Logs UI
    # -------------------------------------------------------------------------

    @app.get("/ui/agent-metrics-logs")
    def agent_metrics_logs_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        logs = database.list_agent_metrics_logs(limit=500)
        agents = database.list_agents()
        definitions = database.list_metric_definitions()
        agent_lookup = {agent.id: agent.name for agent in agents}
        definition_lookup = {defn.id: defn.metric_type for defn in definitions}
        growl_message = _growl_message(request.query_params.get("saved"))
        table_context = _build_table_context(
            database=database,
            request=request,
            user=user,
            model="agent_metrics_logs",
            title="Agent Metrics Logs",
            description="Historical metrics collected from agents",
            create_label=None,
            create_url=None,
            rows=_build_agent_metrics_log_rows(logs, agent_lookup, definition_lookup),
            empty_message="No metrics logs yet.",
        )
        return _render_template(
            request,
            "agent_metrics_logs.html",
            {
                "title": "Agent Metrics Logs",
                "active_nav": "agent_metrics_logs",
                "growl_message": growl_message,
                **table_context,
            },
        )

    return app
