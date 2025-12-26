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

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Query
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware
from slack_sdk.web.client import WebClient

from wintermute.db import Database, utc_now
from wintermute.sources.slack import (
    SLACK_APP_TOKEN_NAME,
    SLACK_BOT_TOKEN_NAME,
    SLACK_PROVIDER,
    SlackSource,
)


class TaskSourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    base_priority: Optional[int] = None
    poll_interval_seconds: Optional[int] = None
    config: Optional[dict[str, Any]] = None


class CredentialCreate(BaseModel):
    name: str
    provider: str
    reference: str


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


def _growl(saved: Optional[str]) -> str:
    messages = {
        "slack": "Saved Slack token data",
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
    }
    if not saved or saved not in messages:
        return ""
    return (
        "<div class=\"growl\"><div class=\"growl-pill\">"
        f"{messages[saved]}"
        "</div></div>"
        "<script>"
        "setTimeout(function(){"
        "var url=new URL(window.location.href);"
        "url.searchParams.delete('saved');"
        "window.history.replaceState({}, '', url.toString());"
        "}, 3200);"
        "</script>"
    )


def _render_page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{title}</title>
    <style>
      :root {{
        --bg: #f5f2ea;
        --ink: #1f1b16;
        --muted: #6f6254;
        --accent: #b3472f;
        --accent-dark: #7b2c1c;
        --panel: #fef9ef;
        --edge: #e2d6c4;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: "Trebuchet MS", "Gill Sans", "Helvetica Neue", Arial, sans-serif;
        background: linear-gradient(180deg, #f7f0e5 0%, #eee2d0 100%);
        color: var(--ink);
        min-height: 100vh;
      }}
      .shell {{
        max-width: 1100px;
        margin: 0 auto;
        padding: 32px 24px 64px;
      }}
      header {{
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 24px 28px;
        background: linear-gradient(120deg, #1f1b16, #3b2e24);
        border-radius: 18px;
        color: #fef9ef;
        box-shadow: 0 18px 40px rgba(0, 0, 0, 0.22);
        position: relative;
        overflow: hidden;
      }}
      h1 {{
        margin: 0;
        font-size: 30px;
        letter-spacing: 2px;
        text-transform: uppercase;
      }}
      h2 {{
        font-family: "Trebuchet MS", "Gill Sans", "Helvetica Neue", Arial, sans-serif;
        font-size: 24px;
        margin-top: 0;
      }}
      main {{
        margin-top: 28px;
      }}
      .panel {{
        background: var(--panel);
        border-radius: 18px;
        padding: 28px;
        box-shadow: 0 16px 36px rgba(0, 0, 0, 0.12);
        border: 1px solid var(--edge);
      }}
      .tile {{
        padding: 16px;
        background: #fffdf7;
        border-radius: 14px;
        border: 1px solid var(--edge);
        box-shadow: inset 0 0 0 1px rgba(179, 71, 47, 0.08);
      }}
      label {{
        display: block;
        margin-bottom: 8px;
        color: var(--muted);
        font-size: 13px;
        letter-spacing: 1px;
        text-transform: uppercase;
      }}
      input, button, select {{
        font-family: inherit;
        padding: 12px 14px;
        border-radius: 10px;
        border: 1px solid var(--edge);
        margin-bottom: 16px;
        width: 100%;
        box-sizing: border-box;
        background: #fffdf8;
      }}
      input[type="checkbox"] {{
        width: auto;
        margin: 0;
      }}
      .checkbox-row {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 16px;
      }}
      .checkbox-row label {{
        margin: 0;
      }}
      .inline-field {{
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 16px;
      }}
      .inline-field select {{
        flex: 1;
        margin-bottom: 0;
      }}
      .inline-link {{
        align-self: center;
        color: var(--accent-dark);
        font-size: 12px;
        text-transform: uppercase;
        letter-spacing: 1px;
        display: flex;
        align-items: center;
        height: 44px;
      }}
      button {{
        background: #1f8a3b;
        color: #ecfff0;
        font-weight: bold;
        border: none;
        cursor: pointer;
        letter-spacing: 1px;
        text-transform: uppercase;
      }}
      button:hover {{
        filter: brightness(1.05);
      }}
      .danger {{
        background: #b3261e;
        color: #fff4f2;
      }}
      .ghost {{
        background: transparent;
        border: 1px solid var(--edge);
        color: var(--ink);
      }}
      .modal {{
        position: fixed;
        inset: 0;
        background: rgba(15, 12, 9, 0.55);
        display: none;
        align-items: center;
        justify-content: center;
        padding: 24px;
        z-index: 1000;
      }}
      .modal.open {{
        display: flex;
      }}
      .modal-card {{
        background: #fffdf7;
        border-radius: 18px;
        padding: 24px;
        width: min(420px, 100%);
        box-shadow: 0 20px 50px rgba(0, 0, 0, 0.25);
      }}
      .modal-actions {{
        display: flex;
        gap: 12px;
        justify-content: flex-end;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 18px;
      }}
      .group {{
        grid-column: 1 / -1;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 18px;
        padding: 18px;
        border-radius: 16px;
        border: 1px dashed var(--edge);
        background: rgba(255, 253, 248, 0.6);
      }}
      .group-title {{
        grid-column: 1 / -1;
        font-size: 14px;
        letter-spacing: 2px;
        text-transform: uppercase;
        color: var(--muted);
        margin: 0;
      }}
      a {{
        color: var(--accent-dark);
      }}
      .muted {{
        color: var(--muted);
      }}
      .subtitle {{
        margin: 0;
        color: #f8efe3;
        font-size: 13px;
        letter-spacing: 1px;
      }}
      .badge {{
        display: inline-block;
        padding: 6px 10px;
        border-radius: 999px;
        background: rgba(255, 245, 233, 0.18);
        border: 1px solid rgba(255, 245, 233, 0.3);
        font-size: 11px;
        letter-spacing: 1px;
        text-transform: uppercase;
      }}
      .nav {{
        display: flex;
        gap: 16px;
        align-items: center;
        margin-top: 16px;
        flex-wrap: wrap;
      }}
      .nav a {{
        text-decoration: none;
        font-size: 13px;
        letter-spacing: 1px;
        text-transform: uppercase;
        color: #f6efe6;
        border-bottom: 2px solid transparent;
        padding-bottom: 2px;
      }}
      .nav a.active {{
        font-weight: bold;
        border-bottom-color: #f6efe6;
      }}
      .growl {{
        position: fixed;
        top: 18px;
        left: 50%;
        transform: translateX(-50%);
        z-index: 999;
        display: flex;
        justify-content: center;
        pointer-events: none;
      }}
      .growl-pill {{
        background: #2f7d32;
        color: #f4fff5;
        padding: 10px 16px;
        border-radius: 999px;
        font-size: 13px;
        letter-spacing: 1px;
        text-transform: uppercase;
        box-shadow: 0 10px 24px rgba(0, 0, 0, 0.2);
        animation: fadeout 0.5s ease-in-out 2.5s forwards;
      }}
      @keyframes fadeout {{
        to {{
          opacity: 0;
          transform: translateY(-6px);
        }}
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <header>
        <div>
          <h1>Foreman Admin</h1>
          <p class="subtitle">Supervisory control room</p>
          <div class="nav">
            <a href="/ui">Home</a>
            <a href="/ui/projects">Projects</a>
            <a href="/ui/tickets">Tickets</a>
            <a href="/ui/vms">VMs</a>
            <a href="/ui/agents">Agents</a>
            <a href="/ui/project-vms">Mappings</a>
          </div>
        </div>
        <span class="badge">Local</span>
      </header>
      <main>{body}</main>
    </div>
  </body>
</html>"""
    return HTMLResponse(
        html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


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
    secret_key = (
        os.environ.get("WINTERMUTE_WEB_SECRET") or secrets.token_urlsafe(32)
    )
    app.add_middleware(SessionMiddleware, secret_key=secret_key)

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
            raise HTTPException(status_code=404, detail="Source not found")
        enabled = form.get("enabled") == "on"
        poll_interval = form.get("poll_interval_seconds")
        config = dict(row.config)
        if source_id == SlackSource.id:
            _update_slack_channel_filter(database)
            config = database.get_task_source(SlackSource.id).config
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
        return RedirectResponse("/ui?saved=project_deleted", status_code=303)

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
                "created_at": row.created_at,
            }
            for row in database.list_credentials()
        ]

    @app.post("/credentials")
    def create_credential(
        payload: CredentialCreate, user: str = Depends(_require_login)
    ) -> dict[str, Any]:
        cred_id = str(uuid.uuid4())
        database.insert_credential(cred_id, payload.name, payload.provider, payload.reference)
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

    @app.get("/ui/projects/{project_id}/edit", response_class=HTMLResponse)
    def edit_project_ui(project_id: str, user: str = Depends(_require_login)) -> HTMLResponse:
        project = database.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        body = f"""
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Edit Project</h3>
              <form method="post" action="/projects/{project.id}">
                <label>Name</label>
                <input type="text" name="name" value="{project.name}" required />
                <label>Slug</label>
                <input type="text" name="slug" value="{project.slug}" required />
                <label>Slack Channel ID</label>
                <input type="text" name="slack_channel_id" value="{project.slack_channel_id or ''}" />
                <button type="submit">Save Project</button>
              </form>
              <button type="button" class="danger" onclick="openDeleteModal()">Delete Project</button>
            </div>
          </div>
        </div>
        <div class="modal" id="delete-modal" aria-hidden="true">
          <div class="modal-card">
            <h3>Are you sure?</h3>
            <p class="muted">This cannot be undone. To delete type <strong>delete me</strong> below.</p>
              <form method="post" action="/projects/{project.id}/delete" onsubmit="return validateDelete()">
                <input type="text" name="confirm" id="delete-confirm" placeholder="delete me" required />
                <div class="checkbox-row">
                  <input type="checkbox" name="delete_slack" />
                  <label>Delete Slack channel too</label>
                </div>
                <div class="modal-actions">
                  <button type="button" class="ghost" onclick="closeDeleteModal()">Cancel</button>
                  <button type="submit" class="danger">Permanently delete project</button>
                </div>
              </form>
          </div>
        </div>
        <script>
          function openDeleteModal() {{
            document.getElementById('delete-modal').classList.add('open');
          }}
          function closeDeleteModal() {{
            document.getElementById('delete-modal').classList.remove('open');
          }}
          function validateDelete() {{
            var value = document.getElementById('delete-confirm').value.trim().toLowerCase();
            if (value !== 'delete me') {{
              alert('Type \"delete me\" to confirm.');
              return false;
            }}
            return true;
          }}
        </script>
        """
        return _render_page("Edit Project", body)

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

    @app.get("/ui/vms/{vm_id}/edit", response_class=HTMLResponse)
    def edit_vm_ui(vm_id: str, user: str = Depends(_require_login)) -> HTMLResponse:
        vm = database.get_vm_target(vm_id)
        if not vm:
            raise HTTPException(status_code=404, detail="VM not found")
        body = f"""
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Edit VM</h3>
              <form method="post" action="/vms/{vm.id}/edit">
                <label>Name</label>
                <input type="text" name="name" value="{vm.name}" required />
                <label>Host</label>
                <input type="text" name="host" value="{vm.host}" required />
                <label>User</label>
                <input type="text" name="user" value="{vm.user}" required />
                <label>Port</label>
                <input type="number" name="port" value="{vm.port}" />
                <button type="submit">Save VM</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Edit VM", body)

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

    @app.get("/ui/agents/{agent_id}/edit", response_class=HTMLResponse)
    def edit_agent_ui(agent_id: str, user: str = Depends(_require_login)) -> HTMLResponse:
        agent = database.get_agent(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        body = f"""
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Edit Agent</h3>
              <form method="post" action="/agents/{agent.id}/edit">
                <label>Name</label>
                <input type="text" name="name" value="{agent.name}" required />
                <label>Slug</label>
                <input type="text" name="slug" value="{agent.slug}" required />
                <label>Command</label>
                <input type="text" name="command" value="{agent.command}" required />
                <label>Required SSH Options</label>
                <input type="text" name="required_ssh_options" value="{agent.required_ssh_options or ''}" />
                <button type="submit">Save Agent</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Edit Agent", body)

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

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        if not database.list_users():
            return RedirectResponse("/setup")
        if not request.session.get("user"):
            return RedirectResponse("/login")
        return RedirectResponse("/ui")

    @app.get("/.well-known/appspecific/com.chrome.devtools.json")
    def chrome_devtools_marker() -> HTMLResponse:
        return HTMLResponse("", status_code=204)

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page() -> HTMLResponse:
        if database.list_users():
            return _render_page(
                "Setup complete", "<div class='panel'>Users already configured.</div>"
            )
        body = """
        <div class="panel">
          <h2>Initial Admin Setup</h2>
          <p class="muted">Create the first administrator account.</p>
          <form method="post" action="/setup">
            <label>Admin username</label>
            <input type="text" name="username" required />
            <label>Password</label>
            <input type="password" name="password" required />
            <button type="submit">Create Admin</button>
          </form>
        </div>
        """
        return _render_page("Setup", body)

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

    @app.get("/login", response_class=HTMLResponse)
    def login_page() -> HTMLResponse:
        body = """
        <div class="panel">
          <h2>Admin Login</h2>
          <p class="muted">Enter your credentials to access the console.</p>
          <form method="post" action="/login">
            <label>Username</label>
            <input type="text" name="username" required />
            <label>Password</label>
            <input type="password" name="password" required />
            <button type="submit">Login</button>
          </form>
        </div>
        """
        return _render_page("Login", body)

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

    @app.get("/ui", response_class=HTMLResponse)
    def ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        status = database.get_supervisor_state()
        work_items = database.fetch_ready_work_items(utc_now())
        projects = database.list_projects()
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
        project_options = "".join(
            f"<option value=\"{project.id}\">{project.name}</option>" for project in projects
        )
        vm_options = "".join(
            f"<option value=\"{vm.id}\">{vm.name} ({vm.host})</option>" for vm in vm_targets
        )
        project_lookup = {project.id: project.name for project in projects}
        vm_lookup = {vm.id: vm.name for vm in vm_targets}
        project_list = "".join(
            f"<li><a href=\"/ui/projects/{project.id}/edit\">{project.name}</a> ({project.slug})</li>"
            for project in projects[:10]
        )
        session_list = "".join(
            f"<li>{session.id} [{session.status}]</li>" for session in sessions[:10]
        )
        growl = _growl(request.query_params.get("saved"))
        body = f"""
        {growl}
        <div class="panel">
          <p class="muted">Signed in as <strong>{user}</strong>. <a href="/logout">Logout</a></p>
          <div class="grid">
            <div class="tile">
              <h3>Supervisor</h3>
              <p>Status: {status.status if status else 'unknown'}</p>
              <p>Current: {status.current_work_id if status else 'n/a'}</p>
              <p>Queue: {status.queue_depth if status else 'n/a'}</p>
              <p>Last action: {status.last_action if status else 'n/a'}</p>
            </div>
            <div class="tile">
              <h3>Queue</h3>
              <p>Ready work items: {len(work_items)}</p>
              <ul>
                {''.join(f"<li>{item.work_id} ({item.priority})</li>" for item in work_items[:5])}
              </ul>
            </div>
            <div class="group">
              <p class="group-title">Slack</p>
              <div class="tile">
                <h3>Slack Tokens</h3>
                <p>Bot token: {"configured" if slack_bot else "missing"}</p>
                <p>App token: {"configured" if slack_app else "missing"}</p>
                <p>Admin user: {"configured" if slack_admin else "missing"}</p>
                <form method="post" action="/slack/credentials">
                  <label>Bot Token (xoxb-...)</label>
                  <input type="password" name="bot_token" placeholder="leave blank to keep existing" />
                  <label>App Token (xapp-...)</label>
                  <input type="password" name="app_token" placeholder="leave blank to keep existing" />
                  <label>Slack User ID to Auto-Invite (U...)</label>
                  <input type="text" name="admin_user_id" placeholder="U0123456789" />
                  <button type="submit">Save Slack Settings</button>
                </form>
              </div>
              <div class="tile">
                <h3>Slack Source</h3>
                <form method="post" action="/sources/slack/ui_update">
                  <div class="checkbox-row">
                    <input type="checkbox" name="enabled" {"checked" if slack_source and slack_source.enabled else ""} />
                    <label>Enabled</label>
                  </div>
                  <label>Poll Interval (seconds)</label>
                  <input type="number" name="poll_interval_seconds" min="1" value="{slack_source.poll_interval_seconds if slack_source else 2}" />
                <label>Channels (auto-managed)</label>
                <input type="text" name="channels" value="{slack_channels}" readonly />
                  <button type="submit">Update Slack Source</button>
                </form>
              </div>
            </div>
            <div class="group">
              <p class="group-title">Projects</p>
              <div class="tile">
                <h3>Create Project</h3>
                <form method="post" action="/projects">
                  <input type="hidden" name="return_to" value="/ui" />
                  <label>Name</label>
                  <input type="text" name="name" required />
                  <label>Slug (optional)</label>
                  <input type="text" name="slug" placeholder="proj-name" />
                  <label>Slack Channel ID (optional)</label>
                  <input type="text" name="slack_channel_id" placeholder="C0123456789" />
                  <button type="submit">Create Project</button>
                </form>
                <p class="muted">Creates a public Slack channel (requires channels:manage) or use a channel ID.</p>
              </div>
              <div class="tile">
                <h3>Projects</h3>
                <ul>
                  {project_list}
                </ul>
              </div>
            </div>
            <div class="group">
              <p class="group-title">VM Targets</p>
              <div class="tile">
                <h3>Add VM</h3>
                <form method="post" action="/vms">
                  <input type="hidden" name="return_to" value="/ui" />
                  <label>Name</label>
                  <input type="text" name="name" required />
                  <label>Host</label>
                  <input type="text" name="host" required />
                  <label>User</label>
                  <input type="text" name="user" required />
                  <label>Port</label>
                  <input type="number" name="port" value="22" />
                  <button type="submit">Add VM</button>
                </form>
              </div>
              <div class="tile">
                <h3>VM Targets</h3>
                <ul>
                  {''.join(f"<li><a href='/ui/vms/{vm.id}/edit'>{vm.name}</a> ({vm.host})</li>" for vm in vm_targets[:10])}
                </ul>
              </div>
            </div>
            <div class="group">
              <p class="group-title">Agents</p>
              <div class="tile">
                <h3>Add Agent</h3>
                <form method="post" action="/agents">
                  <input type="hidden" name="return_to" value="/ui" />
                  <label>Name</label>
                  <input type="text" name="name" required />
                  <label>Slug</label>
                  <input type="text" name="slug" required />
                  <label>Command</label>
                  <input type="text" name="command" required />
                  <label>Required SSH Options</label>
                  <input type="text" name="required_ssh_options" placeholder="-L 1455:localhost:1455" />
                  <button type="submit">Add Agent</button>
                </form>
              </div>
              <div class="tile">
                <h3>Agents</h3>
                <ul>
                  {''.join(f"<li><a href='/ui/agents/{agent.id}/edit'>{agent.name}</a> ({agent.slug})</li>" for agent in agents[:10])}
                </ul>
              </div>
            </div>
            <div class="group">
              <p class="group-title">Project VM Mapping</p>
              <div class="tile">
                <h3>Attach VM</h3>
                <form method="post" action="/project_vms">
                  <input type="hidden" name="return_to" value="/ui" />
                  <label>Project</label>
                  <select name="project_id" required>
                    {project_options}
                  </select>
                  <label>VM Target</label>
                  <select name="vm_target_id" required>
                    {vm_options}
                  </select>
                  <label>Repo Mode</label>
                  <select name="repo_mode">
                    <option value="mirror">mirror</option>
                    <option value="clone">clone</option>
                  </select>
                  <label>Repo Path</label>
                  <input type="text" name="repo_path" />
                  <label>Repo URL (clone mode)</label>
                  <input type="text" name="repo_url" />
                  <button type="submit">Attach VM</button>
                </form>
              </div>
              <div class="tile">
                <h3>Mappings</h3>
                <ul>
                  {''.join(
                    f"<li><a href='/ui/project-vms/{mapping.id}/edit'>{project_lookup.get(mapping.project_id, mapping.project_id)} → {vm_lookup.get(mapping.vm_target_id, mapping.vm_target_id)}</a> ({mapping.repo_mode})</li>"
                    for mapping in project_vms[:10]
                  )}
                </ul>
              </div>
            </div>
            <div class="group">
              <p class="group-title">Sessions</p>
              <div class="tile">
                <h3>Recent Sessions</h3>
                <ul>
                  {session_list}
                </ul>
                <p class="muted">Start from Slack: <code>start projectslug agentslug</code></p>
              </div>
              <div class="tile">
                <h3>Active Agents</h3>
                <p>Available: {len(agents)}</p>
                <p>VM Targets: {len(vm_targets)}</p>
              </div>
            </div>
          </div>
        </div>
        """
        return _render_page("Admin", body)

    @app.get("/ui/projects", response_class=HTMLResponse)
    def projects_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        projects = database.list_projects()
        project_list = "".join(
            f"<li><a href='/ui/projects/{project.id}/edit'>{project.name}</a> ({project.slug})"
            f"<form method='post' action='/projects/{project.id}/delete' style='display:inline; margin-left:8px;'>"
            "<input type='hidden' name='confirm' value='delete me' />"
            "<button type='submit' class='danger'>Delete</button>"
            "</form></li>"
            for project in projects
        )
        body = f"""
        <div class="panel">
          {_growl(request.query_params.get('saved'))}
          <div class="grid">
            <div class="tile">
              <h3>Projects</h3>
              <a href="/ui/projects/create">Create Project</a>
            </div>
            <div class="tile">
              <h3>Project List</h3>
              <ul>{project_list}</ul>
            </div>
          </div>
        </div>
        """
        return _render_page("Projects", body)

    @app.get("/ui/projects/create", response_class=HTMLResponse)
    def projects_create_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        body = """
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Create Project</h3>
              <form method="post" action="/projects">
                <input type="hidden" name="return_to" value="/ui/projects" />
                <label>Name</label>
                <input type="text" name="name" required />
                <label>Slug (optional)</label>
                <input type="text" name="slug" placeholder="proj-name" />
                <label>Slack Channel ID (optional)</label>
                <input type="text" name="slack_channel_id" placeholder="C0123456789" />
                <button type="submit">Create Project</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Create Project", body)

    @app.get("/ui/vms", response_class=HTMLResponse)
    def vms_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        vm_targets = database.list_vm_targets()
        vm_list = "".join(
            f"<li><a href='/ui/vms/{vm.id}/edit'>{vm.name}</a> ({vm.host})"
            f"<form method='post' action='/vms/{vm.id}/delete' style='display:inline; margin-left:8px;'>"
            "<button type='submit' class='danger'>Delete</button>"
            "</form></li>"
            for vm in vm_targets
        )
        body = f"""
        <div class="panel">
          {_growl(request.query_params.get('saved'))}
          <div class="grid">
            <div class="tile">
              <h3>VM Targets</h3>
              <a href="/ui/vms/create">Add VM</a>
            </div>
            <div class="tile">
              <h3>VM List</h3>
              <ul>{vm_list}</ul>
            </div>
          </div>
        </div>
        """
        return _render_page("VM Targets", body)

    @app.get("/ui/vms/create", response_class=HTMLResponse)
    def vms_create_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        body = """
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Add VM</h3>
              <form method="post" action="/vms">
                <input type="hidden" name="return_to" value="/ui/vms" />
                <label>Name</label>
                <input type="text" name="name" required />
                <label>Host</label>
                <input type="text" name="host" required />
                <label>User</label>
                <input type="text" name="user" required />
                <label>Port</label>
                <input type="number" name="port" value="22" />
                <button type="submit">Add VM</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Add VM", body)

    @app.get("/ui/agents", response_class=HTMLResponse)
    def agents_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        agents = database.list_agents()
        agent_list = "".join(
            f"<li><a href='/ui/agents/{agent.id}/edit'>{agent.name}</a> ({agent.slug})"
            f"<form method='post' action='/agents/{agent.id}/delete' style='display:inline; margin-left:8px;'>"
            "<button type='submit' class='danger'>Delete</button>"
            "</form></li>"
            for agent in agents
        )
        body = f"""
        <div class="panel">
          {_growl(request.query_params.get('saved'))}
          <div class="grid">
            <div class="tile">
              <h3>Agents</h3>
              <a href="/ui/agents/create">Add Agent</a>
            </div>
            <div class="tile">
              <h3>Agent List</h3>
              <ul>{agent_list}</ul>
            </div>
          </div>
        </div>
        """
        return _render_page("Agents", body)

    @app.get("/ui/agents/create", response_class=HTMLResponse)
    def agents_create_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        body = """
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Add Agent</h3>
              <form method="post" action="/agents">
                <input type="hidden" name="return_to" value="/ui/agents" />
                <label>Name</label>
                <input type="text" name="name" required />
                <label>Slug</label>
                <input type="text" name="slug" required />
                <label>Command</label>
                <input type="text" name="command" required />
                <label>Required SSH Options</label>
                <input type="text" name="required_ssh_options" placeholder="-L 1455:localhost:1455" />
                <button type="submit">Add Agent</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Add Agent", body)

    @app.get("/ui/project-vms", response_class=HTMLResponse)
    def project_vms_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        project_vms = database.list_project_vms()
        project_lookup = {project.id: project.name for project in database.list_projects()}
        vm_lookup = {vm.id: vm.name for vm in database.list_vm_targets()}
        mapping_list = "".join(
            f"<li><a href='/ui/project-vms/{mapping.id}/edit'>{project_lookup.get(mapping.project_id, mapping.project_id)} "
            f"→ {vm_lookup.get(mapping.vm_target_id, mapping.vm_target_id)}</a> "
            f"({mapping.repo_mode})"
            f"<form method='post' action='/project_vms/{mapping.id}/delete' style='display:inline; margin-left:8px;'>"
            "<button type='submit' class='danger'>Delete</button>"
            "</form></li>"
            for mapping in project_vms
        )
        body = f"""
        <div class="panel">
          {_growl(request.query_params.get('saved'))}
          <div class="grid">
            <div class="tile">
              <h3>Project VM Mappings</h3>
              <a href="/ui/project-vms/create">Attach VM</a>
            </div>
            <div class="tile">
              <h3>Mappings</h3>
              <ul>{mapping_list}</ul>
            </div>
          </div>
        </div>
        """
        return _render_page("Mappings", body)

    @app.get("/ui/project-vms/create", response_class=HTMLResponse)
    def project_vms_create_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        projects = database.list_projects()
        vm_targets = database.list_vm_targets()
        project_options = "".join(
            f"<option value='{project.id}'>{project.name}</option>" for project in projects
        )
        vm_options = "".join(
            f"<option value='{vm.id}'>{vm.name} ({vm.host})</option>" for vm in vm_targets
        )
        body = f"""
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Attach VM</h3>
              <form method="post" action="/project_vms">
                <input type="hidden" name="return_to" value="/ui/project-vms" />
                <label>Project</label>
                <select name="project_id" required>
                  {project_options}
                </select>
                <label>VM Target</label>
                <select name="vm_target_id" required>
                  {vm_options}
                </select>
                <label>Repo Mode</label>
                <select name="repo_mode">
                  <option value="mirror">mirror</option>
                  <option value="clone">clone</option>
                </select>
                <label>Repo Path</label>
                <input type="text" name="repo_path" />
                <label>Repo URL (clone mode)</label>
                <input type="text" name="repo_url" />
                <button type="submit">Attach VM</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Attach VM", body)

    @app.get("/ui/project-vms/{mapping_id}/edit", response_class=HTMLResponse)
    def project_vms_edit_ui(mapping_id: str, user: str = Depends(_require_login)) -> HTMLResponse:
        mapping = database.get_project_vm(mapping_id)
        if not mapping:
            raise HTTPException(status_code=404, detail="Mapping not found")
        projects = database.list_projects()
        vm_targets = database.list_vm_targets()
        project_options = "".join(
            f"<option value='{project.id}' {'selected' if project.id == mapping.project_id else ''}>{project.name}</option>"
            for project in projects
        )
        vm_options = "".join(
            f"<option value='{vm.id}' {'selected' if vm.id == mapping.vm_target_id else ''}>{vm.name} ({vm.host})</option>"
            for vm in vm_targets
        )
        body = f"""
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Edit Mapping</h3>
              <form method="post" action="/project_vms/{mapping.id}/edit">
                <label>Project</label>
                <div class="inline-field">
                  <select name="project_id" required>
                    {project_options}
                  </select>
                  <a class="inline-link" href="/ui/projects/{mapping.project_id}/edit">Edit project</a>
                </div>
                <label>VM Target</label>
                <div class="inline-field">
                  <select name="vm_target_id" required>
                    {vm_options}
                  </select>
                  <a class="inline-link" href="/ui/vms/{mapping.vm_target_id}/edit">Edit VM target</a>
                </div>
                <label>Repo Mode</label>
                <select name="repo_mode">
                  <option value="mirror" {"selected" if mapping.repo_mode == "mirror" else ""}>mirror</option>
                  <option value="clone" {"selected" if mapping.repo_mode == "clone" else ""}>clone</option>
                </select>
                <label>Repo Path</label>
                <input type="text" name="repo_path" value="{mapping.repo_path or ''}" />
                <label>Repo URL (clone mode)</label>
                <input type="text" name="repo_url" value="{mapping.repo_url or ''}" />
                <button type="submit">Save Mapping</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Edit Mapping", body)

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
    @app.get("/ui/tickets", response_class=HTMLResponse)
    def tickets_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        tickets = database.list_tickets()
        ticket_list = "".join(
            f"<li>{ticket.title} [{ticket.status}]"
            f"<form method='post' action='/tickets/{ticket.id}/delete' style='display:inline; margin-left:8px;'>"
            "<button type='submit' class='danger'>Delete</button>"
            "</form></li>"
            for ticket in tickets[:50]
        )
        body = f"""
        <div class="panel">
          {_growl(request.query_params.get("saved"))}
          <div class="grid">
            <div class="tile">
              <h3>Tickets</h3>
              <p class="muted">All current tickets.</p>
              <a href="/ui/tickets/create">Create Ticket</a>
            </div>
            <div class="tile">
              <h3>Ticket List</h3>
              <ul>
                {ticket_list}
              </ul>
            </div>
          </div>
        </div>
        """
        return _render_page("Tickets", body)

    @app.get("/ui/tickets/create", response_class=HTMLResponse)
    def tickets_create_ui(request: Request, user: str = Depends(_require_login)) -> HTMLResponse:
        projects = database.list_projects()
        project_options = "".join(
            f"<option value=\"{project.id}\">{project.name}</option>" for project in projects
        )
        body = f"""
        <div class="panel">
          <div class="grid">
            <div class="tile">
              <h3>Create Ticket</h3>
              <form method="post" action="/tickets">
                <input type="hidden" name="return_to" value="/ui/tickets" />
                <label>Project</label>
                <select name="project_id" required>
                  {project_options}
                </select>
                <label>Title</label>
                <input type="text" name="title" required />
                <label>Description</label>
                <input type="text" name="description" />
                <label>Assigned To</label>
                <input type="text" name="assigned_to" />
                <label>Estimate</label>
                <input type="text" name="estimate" />
                <label>Status</label>
                <input type="text" name="status" value="open" />
                <button type="submit">Create Ticket</button>
              </form>
            </div>
          </div>
        </div>
        """
        return _render_page("Create Ticket", body)
    return app
