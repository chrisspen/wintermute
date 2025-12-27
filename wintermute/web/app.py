"""FastAPI admin console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import uuid
from typing import Any, Optional

import aiohttp
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi import Query
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from slack_sdk.web.client import WebClient

from wintermute.db import Database, utc_now
from wintermute.sources.github import GitHubIssuesSource
from wintermute.sources.slack import (
    SLACK_APP_TOKEN_NAME,
    SLACK_BOT_TOKEN_NAME,
    SLACK_PROVIDER,
    SlackSource,
)
from wintermute.tools.github import GITHUB_PROVIDER, GITHUB_TOKEN_NAME


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


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "project"


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


def _growl_message(saved: Optional[str]) -> Optional[str]:
    messages = {
        "slack": "Saved Slack token data",
        "github": "Saved GitHub token data",
        "project_created": "Project created",
        "project_updated": "Project updated",
        "project_deleted": "Deletion of project successful",
        "ticket_created": "Ticket created",
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
        "slack_source": "Saved Slack source settings",
        "github_source_created": "GitHub source created",
        "github_source_updated": "GitHub source updated",
        "github_source_deleted": "GitHub source deleted",
        "github_token_created": "GitHub token created",
        "github_token_updated": "GitHub token updated",
        "github_token_deleted": "GitHub token deleted",
    }
    if not saved:
        return None
    return messages.get(saved)


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
    app = FastAPI(title="Foreman Admin")
    base_dir = os.path.dirname(__file__)
    templates = Jinja2Templates(directory=os.path.join(base_dir, "templates"))
    app.mount("/static", StaticFiles(directory=os.path.join(base_dir, "static")), name="static")
    secret_key = (
        os.environ.get("WINTERMUTE_WEB_SECRET") or secrets.token_urlsafe(32)
    )
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

    def _render_template(request: Request, template_name: str, context: dict[str, Any]) -> Response:
        response = templates.TemplateResponse(
            template_name,
            {"request": request, **context},
        )
        response.headers.update(
            {
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            }
        )
        return response

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
            result.append(
                {
                    "id": row.id,
                    "enabled": bool(row.enabled),
                    "base_priority": row.base_priority,
                    "poll_interval_seconds": row.poll_interval_seconds,
                    "config": row.config,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
            )
        return result

    @app.put("/sources/{source_id}")
    def update_source(
        source_id: str, payload: TaskSourceUpdate, user: str = Depends(_require_login)
    ) -> dict[str, Any]:
        rows = {row.id: row for row in database.list_task_sources()}
        row = rows.get(source_id)
        if not row:
            raise HTTPException(status_code=404, detail="Source not found")
        database.upsert_task_source(
            source_id,
            payload.enabled if payload.enabled is not None else bool(row.enabled),
            payload.base_priority if payload.base_priority is not None else row.base_priority,
            payload.poll_interval_seconds
            if payload.poll_interval_seconds is not None
            else row.poll_interval_seconds,
            payload.config if payload.config is not None else row.config,
        )
        return {"status": "ok"}

    @app.post("/sources/{source_id}/ui_update")
    async def update_source_ui(
        source_id: str, request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
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
        saved = "github_source" if source_id == GitHubIssuesSource.id else "slack_source"
        return RedirectResponse(f"/ui?saved={saved}", status_code=303)

    @app.get("/work-items")
    def list_work_items(
        status: Optional[str] = None, user: str = Depends(_require_login)
    ) -> list[dict[str, Any]]:
        rows = database.list_work_items(status=status)
        return [
            {
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
            }
            for row in rows
        ]

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
        return [
            {
                "id": row.id,
                "name": row.name,
                "provider": row.provider,
                "reference": row.reference,
                "note": row.note,
                "created_at": row.created_at,
            }
            for row in database.list_credentials()
        ]

    @app.post("/credentials")
    def create_credential(
        payload: CredentialCreate, user: str = Depends(_require_login)
    ) -> dict[str, Any]:
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
    async def set_slack_credentials(
        request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
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
    async def create_github_token(
        request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
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
    async def update_github_token(
        token_id: str, request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
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
    async def delete_github_token(
        token_id: str, user: str = Depends(_require_login)
    ) -> RedirectResponse:
        database.delete_github_token(token_id)
        return RedirectResponse("/ui/github-tokens?saved=github_token_deleted", status_code=303)

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
        slug = slug_raw or f"proj-{_slugify(name)}"
        channel_name = slug
        channel_id = None
        channel_id_raw = str(form.get("slack_channel_id", "")).strip()
        if channel_id_raw:
            channel_id = channel_id_raw
        if not channel_id:
            client = _slack_client(database)
            try:
                resp = client.conversations_create(name=channel_name, is_private=False)
                channel_id = resp.get("channel", {}).get("id")
            except Exception as exc:
                error_text = str(exc)
                if "name_taken" in error_text:
                    channel_id = _find_channel_id(client, channel_name)
                elif "missing_scope" in error_text:
                    raise HTTPException(
                        status_code=400,
                        detail="Slack channel create failed (missing scope: channels:manage).",
                    ) from exc
                else:
                    raise HTTPException(
                        status_code=400, detail=f"Slack channel create failed: {exc}"
                    ) from exc
        if not channel_id:
            raise HTTPException(status_code=400, detail="Slack channel id missing")
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
        project_id = str(uuid.uuid4())
        database.insert_project(project_id, name, slug, channel_id)
        _update_slack_channel_filter(database)
        return RedirectResponse(f"{return_to}?saved=project_created", status_code=303)

    @app.get("/ui/projects/{project_id}/edit")
    def edit_project_ui(project_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return _render_template(
            request,
            "project_edit.html",
            {
                "title": "Edit Project",
                "active_nav": "projects",
                "growl_message": None,
                "project": project,
            },
        )

    @app.post("/projects/{project_id}")
    async def update_project(project_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        form = await request.form()
        name = str(form.get("name", "")).strip()
        slug = str(form.get("slug", "")).strip()
        channel_id = str(form.get("slack_channel_id", "")).strip() or None
        if not name or not slug:
            raise HTTPException(status_code=400, detail="Missing name or slug")
        database.update_project(project_id, name=name, slug=slug, slack_channel_id=channel_id)
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

    @app.post("/tickets")
    async def create_ticket(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        title = str(form.get("title", "")).strip()
        description = str(form.get("description", "")).strip() or None
        assigned_to = str(form.get("assigned_to", "")).strip() or None
        estimate = str(form.get("estimate", "")).strip() or None
        status = str(form.get("status", "open")).strip() or "open"
        return_to = str(form.get("return_to", "/ui/tickets")).strip() or "/ui/tickets"
        if not return_to.startswith("/ui"):
            return_to = "/ui/tickets"
        if not project_id or not title:
            raise HTTPException(status_code=400, detail="Missing project or title")
        database.insert_ticket(
            ticket_id=str(uuid.uuid4()),
            project_id=project_id,
            title=title,
            description=description,
            assigned_to=assigned_to,
            estimate=estimate,
            status=status,
        )
        return RedirectResponse(f"{return_to}?saved=ticket_created", status_code=303)

    @app.post("/tickets/{ticket_id}/delete")
    async def delete_ticket(ticket_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_ticket(ticket_id)
        return RedirectResponse("/ui/tickets?saved=ticket_deleted", status_code=303)

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
        if not name or not host or not user_name:
            raise HTTPException(status_code=400, detail="Missing VM fields")
        database.update_vm_target(vm_id, name=name, host=host, user=user_name, port=port)
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
        ssh_options = str(form.get("required_ssh_options", "")).strip() or None
        return_to = str(form.get("return_to", "/ui/agents")).strip() or "/ui/agents"
        if not return_to.startswith("/ui"):
            return_to = "/ui/agents"
        if not name or not slug or not command:
            raise HTTPException(status_code=400, detail="Missing agent fields")
        database.insert_agent(str(uuid.uuid4()), name, slug, command, ssh_options)
        return RedirectResponse(f"{return_to}?saved=agent_created", status_code=303)

    @app.get("/ui/agents/{agent_id}/edit")
    def edit_agent_ui(agent_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        return _render_template(
            request,
            "agent_edit.html",
            {
                "title": "Edit Agent",
                "active_nav": "agents",
                "growl_message": None,
                "agent": agent,
            },
        )

    @app.post("/agents/{agent_id}/edit")
    async def update_agent(agent_id: str, request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        name = str(form.get("name", "")).strip()
        slug = str(form.get("slug", "")).strip()
        command = str(form.get("command", "")).strip()
        ssh_options = str(form.get("required_ssh_options", "")).strip() or None
        if not name or not slug or not command:
            raise HTTPException(status_code=400, detail="Missing agent fields")
        database.update_agent(agent_id, name=name, slug=slug, command=command, required_ssh_options=ssh_options)
        return RedirectResponse("/ui/agents?saved=agent_updated", status_code=303)

    @app.post("/agents/{agent_id}/delete")
    async def delete_agent(agent_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_agent(agent_id)
        return RedirectResponse("/ui/agents?saved=agent_deleted", status_code=303)

    @app.post("/project_vms")
    async def create_project_vm(
        request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        vm_target_id = str(form.get("vm_target_id", "")).strip()
        repo_mode = str(form.get("repo_mode", "mirror")).strip()
        repo_path = str(form.get("repo_path", "")).strip() or None
        repo_url = str(form.get("repo_url", "")).strip() or None
        return_to = str(form.get("return_to", "/ui/project-vms")).strip() or "/ui/project-vms"
        if not return_to.startswith("/ui"):
            return_to = "/ui/project-vms"
        if not project_id or not vm_target_id:
            raise HTTPException(status_code=400, detail="Missing project or VM")
        database.insert_project_vm(
            project_vm_id=str(uuid.uuid4()),
            project_id=project_id,
            vm_target_id=vm_target_id,
            repo_mode=repo_mode,
            repo_path=repo_path,
            repo_url=repo_url,
        )
        return RedirectResponse(f"{return_to}?saved=mapping_created", status_code=303)

    @app.post("/project_vms/{mapping_id}/delete")
    async def delete_project_vm(mapping_id: str, user: str = Depends(_require_login)) -> RedirectResponse:
        database.delete_project_vm(mapping_id)
        return RedirectResponse("/ui/project-vms?saved=mapping_deleted", status_code=303)

    @app.get("/logs/tail")
    def tail_logs(
        limit: int = Query(default=100, ge=1, le=1000), user: str = Depends(_require_login)
    ) -> dict[str, Any]:
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
        status = database.get_supervisor_state()
        work_items = database.fetch_ready_work_items(utc_now())
        projects = database.list_projects()
        tickets = database.list_tickets()
        vm_targets = database.list_vm_targets()
        agents = database.list_agents()
        project_vms = database.list_project_vms()
        sessions = database.list_sessions()
        slack_source = database.get_task_source("slack")
        slack_config = slack_source.config if slack_source else {}
        slack_channels = ", ".join(slack_config.get("channels", []))
        slack_bot = database.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        slack_app = database.get_credential_by_name(SLACK_PROVIDER, SLACK_APP_TOKEN_NAME)
        slack_admin = database.get_credential_by_name(SLACK_PROVIDER, "admin_user_id")
        github_tokens = database.list_github_tokens()
        github_sources = database.list_github_sources()
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "admin.html",
            {
                "title": "Admin",
                "active_nav": "home",
                "growl_message": growl_message,
                "user": user,
                "status": status,
                "work_items": work_items,
                "projects": projects,
                "tickets": tickets,
                "vm_targets": vm_targets,
                "agents": agents,
                "project_vms": project_vms,
                "sessions": sessions,
                "slack_source": slack_source,
                "slack_channels": slack_channels,
                "slack_bot": slack_bot,
                "slack_app": slack_app,
                "slack_admin": slack_admin,
                "github_tokens": github_tokens,
                "github_sources": github_sources,
                "project_lookup": {project.id: project.name for project in projects},
                "vm_lookup": {vm.id: vm.name for vm in vm_targets},
            },
        )

    @app.get("/ui/projects")
    def projects_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "projects.html",
            {
                "title": "Projects",
                "active_nav": "projects",
                "growl_message": growl_message,
                "projects": projects,
            },
        )

    @app.get("/ui/projects/create")
    def projects_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/projects")
        if not return_to.startswith("/ui"):
            return_to = "/ui/projects"
        return _render_template(
            request,
            "project_create.html",
            {
                "title": "Create Project",
                "active_nav": "projects",
                "growl_message": None,
                "return_to": return_to,
            },
        )

    @app.get("/ui/vms")
    def vms_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        vm_targets = database.list_vm_targets()
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "vms.html",
            {
                "title": "VM Targets",
                "active_nav": "vms",
                "growl_message": growl_message,
                "vm_targets": vm_targets,
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
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "agents.html",
            {
                "title": "Agents",
                "active_nav": "agents",
                "growl_message": growl_message,
                "agents": agents,
            },
        )

    @app.get("/ui/agents/create")
    def agents_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/agents")
        if not return_to.startswith("/ui"):
            return_to = "/ui/agents"
        return _render_template(
            request,
            "agent_create.html",
            {
                "title": "Add Agent",
                "active_nav": "agents",
                "growl_message": None,
                "return_to": return_to,
            },
        )

    @app.get("/ui/project-vms")
    def project_vms_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        project_vms = database.list_project_vms()
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "project_vms.html",
            {
                "title": "Mappings",
                "active_nav": "mappings",
                "growl_message": growl_message,
                "project_vms": project_vms,
                "project_lookup": {project.id: project.name for project in database.list_projects()},
                "vm_lookup": {vm.id: vm.name for vm in database.list_vm_targets()},
            },
        )

    @app.get("/ui/project-vms/create")
    def project_vms_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        vm_targets = database.list_vm_targets()
        return_to = request.query_params.get("return_to", "/ui/project-vms")
        if not return_to.startswith("/ui"):
            return_to = "/ui/project-vms"
        return _render_template(
            request,
            "project_vm_create.html",
            {
                "title": "Attach VM",
                "active_nav": "mappings",
                "growl_message": None,
                "projects": projects,
                "vm_targets": vm_targets,
                "return_to": return_to,
            },
        )

    @app.get("/ui/github-sources")
    def github_sources_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        sources = database.list_github_sources()
        github_task_source = database.get_task_source(GitHubIssuesSource.id)
        legacy_config = github_task_source.config if github_task_source else {}
        project_lookup = {project.id: project.name for project in database.list_projects()}
        token_lookup = {
            token.id: token.note or token.user_login or token.id
            for token in database.list_github_tokens()
        }
        agent_lookup = {agent.id: agent.name for agent in database.list_agents()}
        legacy_project_id = str(legacy_config.get("project_id", "")).strip()
        legacy_project_name = project_lookup.get(legacy_project_id, "unset") if legacy_project_id else "unset"
        legacy_owner = str(legacy_config.get("owner", "")).strip()
        legacy_repo = str(legacy_config.get("repo", "")).strip()
        legacy_state = str(legacy_config.get("state", "")).strip()
        legacy_labels = ", ".join(legacy_config.get("labels", []) or [])
        legacy_has_config = bool(legacy_owner or legacy_repo or legacy_state or legacy_labels or legacy_project_id)
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "github_sources.html",
            {
                "title": "GitHub Sources",
                "active_nav": "github_sources",
                "growl_message": growl_message,
                "sources": sources,
                "github_task_source": github_task_source,
                "project_lookup": project_lookup,
                "token_lookup": token_lookup,
                "agent_lookup": agent_lookup,
                "legacy_config": legacy_has_config,
                "legacy_project_name": legacy_project_name,
                "legacy_owner": legacy_owner,
                "legacy_repo": legacy_repo,
                "legacy_state": legacy_state,
                "legacy_labels": legacy_labels,
            },
        )

    @app.get("/ui/github-tokens")
    def github_tokens_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        tokens = database.list_github_tokens()
        legacy_token = database.get_credential_by_name(GITHUB_PROVIDER, GITHUB_TOKEN_NAME)
        legacy_user_id = database.get_credential_by_name(GITHUB_PROVIDER, "user_id")
        legacy_user_login = database.get_credential_by_name(GITHUB_PROVIDER, "user_login")
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "github_tokens.html",
            {
                "title": "GitHub Tokens",
                "active_nav": "github_tokens",
                "growl_message": growl_message,
                "tokens": tokens,
                "legacy_token": bool(legacy_token),
                "legacy_user_id": legacy_user_id.reference if legacy_user_id else "",
                "legacy_user_login": legacy_user_login.reference if legacy_user_login else "",
            },
        )

    @app.get("/ui/github-tokens/create")
    def github_tokens_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        return_to = request.query_params.get("return_to", "/ui/github-tokens")
        if not return_to.startswith("/ui"):
            return_to = "/ui/github-tokens"
        return _render_template(
            request,
            "github_token_create.html",
            {
                "title": "Add GitHub Token",
                "active_nav": "github_tokens",
                "growl_message": None,
                "return_to": return_to,
            },
        )

    @app.get("/ui/github-tokens/{token_id}/edit")
    def github_tokens_edit_ui(token_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        token = database.get_github_token(token_id)
        if not token:
            raise HTTPException(status_code=404, detail="GitHub token not found")
        return _render_template(
            request,
            "github_token_edit.html",
            {
                "title": "Edit GitHub Token",
                "active_nav": "github_tokens",
                "growl_message": None,
                "token": token,
            },
        )

    @app.get("/ui/github-sources/create")
    def github_sources_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        tokens = database.list_github_tokens()
        agents = database.list_agents()
        return_to = request.query_params.get("return_to", "/ui/github-sources")
        if not return_to.startswith("/ui"):
            return_to = "/ui/github-sources"
        token_notice = "" if tokens else "Add a GitHub token before creating a source."
        return _render_template(
            request,
            "github_source_create.html",
            {
                "title": "Add GitHub Source",
                "active_nav": "github_sources",
                "growl_message": None,
                "projects": projects,
                "tokens": tokens,
                "agents": agents,
                "return_to": return_to,
                "token_notice": token_notice if token_notice and not tokens else "",
            },
        )

    @app.get("/ui/github-sources/{source_id}/edit")
    def github_sources_edit_ui(source_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        source = database.get_github_source(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="GitHub source not found")
        projects = database.list_projects()
        tokens = database.list_github_tokens()
        agents = database.list_agents()
        labels = ", ".join(source.labels)
        return _render_template(
            request,
            "github_source_edit.html",
            {
                "title": "Edit GitHub Source",
                "active_nav": "github_sources",
                "growl_message": None,
                "source": source,
                "projects": projects,
                "tokens": tokens,
                "agents": agents,
                "labels": labels,
            },
        )

    @app.post("/github-sources")
    async def create_github_source(request: Request, user: str = Depends(_require_login)) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        token_id = str(form.get("token_id", "")).strip()
        agent_id = str(form.get("agent_id", "")).strip() or None
        owner = str(form.get("owner", "")).strip()
        repo = str(form.get("repo", "")).strip()
        state = str(form.get("state", "open")).strip() or "open"
        labels_raw = str(form.get("labels", "")).strip()
        labels = _parse_labels(labels_raw)
        enabled = form.get("enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        if not project_id or not token_id or not owner or not repo:
            raise HTTPException(status_code=400, detail="Missing GitHub source fields")
        if not database.get_github_token(token_id):
            raise HTTPException(status_code=400, detail="GitHub token not found")
        if agent_id and not database.get_agent(agent_id):
            raise HTTPException(status_code=400, detail="Agent not found")
        if auto_start and not agent_id:
            raise HTTPException(status_code=400, detail="Agent is required for auto-start")
        database.insert_github_source(
            str(uuid.uuid4()),
            token_id=token_id,
            agent_id=agent_id,
            project_id=project_id,
            owner=owner,
            repo=repo,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
        )
        return_to = str(form.get("return_to", "/ui/github-sources")).strip() or "/ui/github-sources"
        if not return_to.startswith("/ui"):
            return_to = "/ui/github-sources"
        return RedirectResponse(f"{return_to}?saved=github_source_created", status_code=303)

    @app.post("/github-sources/{source_id}/edit")
    async def update_github_source(
        source_id: str, request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        token_id = str(form.get("token_id", "")).strip()
        agent_id = str(form.get("agent_id", "")).strip() or None
        owner = str(form.get("owner", "")).strip()
        repo = str(form.get("repo", "")).strip()
        state = str(form.get("state", "open")).strip() or "open"
        labels_raw = str(form.get("labels", "")).strip()
        labels = _parse_labels(labels_raw)
        enabled = form.get("enabled") == "on"
        auto_start = form.get("auto_start") == "on"
        if not project_id or not token_id or not owner or not repo:
            raise HTTPException(status_code=400, detail="Missing GitHub source fields")
        if agent_id and not database.get_agent(agent_id):
            raise HTTPException(status_code=400, detail="Agent not found")
        if auto_start and not agent_id:
            raise HTTPException(status_code=400, detail="Agent is required for auto-start")
        database.update_github_source(
            source_id,
            token_id=token_id,
            agent_id=agent_id,
            project_id=project_id,
            owner=owner,
            repo=repo,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
        )
        return RedirectResponse("/ui/github-sources?saved=github_source_updated", status_code=303)

    @app.post("/github-sources/{source_id}/delete")
    async def delete_github_source(
        source_id: str, user: str = Depends(_require_login)
    ) -> RedirectResponse:
        database.delete_github_source(source_id)
        return RedirectResponse("/ui/github-sources?saved=github_source_deleted", status_code=303)

    @app.get("/ui/project-vms/{mapping_id}/edit")
    def project_vms_edit_ui(mapping_id: str, request: Request, user: str = Depends(_require_login)) -> Response:
        mapping = database.get_project_vm(mapping_id)
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")
        projects = database.list_projects()
        vm_targets = database.list_vm_targets()
        return _render_template(
            request,
            "project_vm_edit.html",
            {
                "title": "Edit Mapping",
                "active_nav": "mappings",
                "growl_message": None,
                "mapping": mapping,
                "projects": projects,
                "vm_targets": vm_targets,
            },
        )

    @app.post("/project_vms/{mapping_id}/edit")
    async def project_vms_update(
        mapping_id: str, request: Request, user: str = Depends(_require_login)
    ) -> RedirectResponse:
        form = await request.form()
        project_id = str(form.get("project_id", "")).strip()
        vm_target_id = str(form.get("vm_target_id", "")).strip()
        repo_mode = str(form.get("repo_mode", "mirror")).strip()
        repo_path = str(form.get("repo_path", "")).strip() or None
        repo_url = str(form.get("repo_url", "")).strip() or None
        if not project_id or not vm_target_id:
            raise HTTPException(status_code=400, detail="Missing project or VM")
        database.update_project_vm(
            mapping_id,
            project_id=project_id,
            vm_target_id=vm_target_id,
            repo_mode=repo_mode,
            repo_path=repo_path,
            repo_url=repo_url,
        )
        return RedirectResponse("/ui/project-vms?saved=mapping_updated", status_code=303)
    @app.get("/ui/tickets")
    def tickets_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        tickets = database.list_tickets()
        growl_message = _growl_message(request.query_params.get("saved"))
        return _render_template(
            request,
            "tickets.html",
            {
                "title": "Tickets",
                "active_nav": "tickets",
                "growl_message": growl_message,
                "tickets": tickets,
            },
        )

    @app.get("/ui/tickets/create")
    def tickets_create_ui(request: Request, user: str = Depends(_require_login)) -> Response:
        projects = database.list_projects()
        return_to = request.query_params.get("return_to", "/ui/tickets")
        if not return_to.startswith("/ui"):
            return_to = "/ui/tickets"
        return _render_template(
            request,
            "ticket_create.html",
            {
                "title": "Create Ticket",
                "active_nav": "tickets",
                "growl_message": None,
                "projects": projects,
                "return_to": return_to,
            },
        )
    return app
