"""FastAPI admin console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import uuid
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Query
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

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
      button {{
        background: linear-gradient(135deg, var(--accent), var(--accent-dark));
        color: #fff5e9;
        font-weight: bold;
        border: none;
        cursor: pointer;
        letter-spacing: 1px;
        text-transform: uppercase;
      }}
      button:hover {{
        filter: brightness(1.05);
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
    </style>
  </head>
  <body>
    <div class="shell">
      <header>
        <div>
          <h1>Foreman Admin</h1>
          <p class="subtitle">Supervisory control room</p>
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
        channels_raw = str(form.get("channels", ""))
        config = dict(row.config)
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
        return RedirectResponse("/ui", status_code=303)

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
        if not bot_token or not app_token:
            raise HTTPException(status_code=400, detail="Missing Slack tokens")
        database.upsert_credential(
            cred_id=f"{SLACK_PROVIDER}:{SLACK_BOT_TOKEN_NAME}",
            name=SLACK_BOT_TOKEN_NAME,
            provider=SLACK_PROVIDER,
            reference=bot_token,
        )
        database.upsert_credential(
            cred_id=f"{SLACK_PROVIDER}:{SLACK_APP_TOKEN_NAME}",
            name=SLACK_APP_TOKEN_NAME,
            provider=SLACK_PROVIDER,
            reference=app_token,
        )
        return RedirectResponse("/ui", status_code=303)

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
        slack_source = database.get_task_source("slack")
        slack_config = slack_source.config if slack_source else {}
        slack_channels = ", ".join(slack_config.get("channels", []))
        slack_bot = database.get_credential_by_name(SLACK_PROVIDER, SLACK_BOT_TOKEN_NAME)
        slack_app = database.get_credential_by_name(SLACK_PROVIDER, SLACK_APP_TOKEN_NAME)
        body = f"""
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
                <form method="post" action="/slack/credentials">
                  <label>Bot Token (xoxb-...)</label>
                  <input type="password" name="bot_token" required />
                  <label>App Token (xapp-...)</label>
                  <input type="password" name="app_token" required />
                  <button type="submit">Save Tokens</button>
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
                  <label>Channels (comma-separated IDs)</label>
                  <input type="text" name="channels" value="{slack_channels}" />
                  <button type="submit">Update Slack Source</button>
                </form>
              </div>
            </div>
          </div>
        </div>
        """
        return _render_page("Admin", body)

    return app
