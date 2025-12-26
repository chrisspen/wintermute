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
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from wintermute.db import Database, utc_now


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


def _render_page(title: str, body: str) -> HTMLResponse:
    html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>{title}</title>
    <style>
      :root {{
        --bg: #f3f0e8;
        --panel: #fff8ea;
        --accent: #2e4b3c;
        --muted: #7a6a52;
      }}
      body {{
        margin: 0;
        font-family: "Courier New", Courier, monospace;
        background: radial-gradient(circle at top, #fdf6e3, #eadbc8);
        color: #2d2a26;
      }}
      header {{
        padding: 24px 32px;
        background: linear-gradient(120deg, #2e4b3c, #496b55);
        color: #f8f5ef;
      }}
      h1 {{
        margin: 0;
        font-size: 28px;
        letter-spacing: 1px;
      }}
      main {{
        padding: 32px;
      }}
      .panel {{
        background: var(--panel);
        border-radius: 12px;
        padding: 24px;
        box-shadow: 0 12px 30px rgba(0, 0, 0, 0.12);
      }}
      label {{
        display: block;
        margin-bottom: 6px;
        color: var(--muted);
        font-size: 14px;
      }}
      input, button, select {{
        font-family: inherit;
        padding: 10px 12px;
        border-radius: 8px;
        border: 1px solid #cbbfa9;
        margin-bottom: 16px;
        width: 100%;
        box-sizing: border-box;
      }}
      button {{
        background: var(--accent);
        color: #fff8ea;
        font-weight: bold;
        border: none;
        cursor: pointer;
      }}
      .grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 16px;
      }}
      a {{
        color: #2e4b3c;
      }}
      .muted {{
        color: var(--muted);
      }}
      .item {{
        padding: 12px;
        background: #fffdf7;
        border-radius: 8px;
        border: 1px solid #e0d7c6;
      }}
    </style>
  </head>
  <body>
    <header><h1>Foreman Admin</h1></header>
    <main>{body}</main>
  </body>
</html>"""
    return HTMLResponse(html)


def create_app(db: Optional[Database] = None) -> FastAPI:
    database = db or Database()
    database.initialize()
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

    @app.get("/logs/tail")
    def tail_logs(
        limit: int = Field(default=100, ge=1, le=1000), user: str = Depends(_require_login)
    ) -> dict[str, Any]:
        return {"entries": [], "limit": limit}

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request) -> HTMLResponse:
        if not database.list_users():
            return RedirectResponse("/setup")
        if not request.session.get("user"):
            return RedirectResponse("/login")
        return RedirectResponse("/ui")

    @app.get("/setup", response_class=HTMLResponse)
    def setup_page() -> HTMLResponse:
        if database.list_users():
            return _render_page(
                "Setup complete", "<div class='panel'>Users already configured.</div>"
            )
        body = """
        <div class="panel">
          <h2>Initial Admin Setup</h2>
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
        body = f"""
        <div class="panel">
          <p class="muted">Signed in as <strong>{user}</strong>. <a href="/logout">Logout</a></p>
          <div class="grid">
            <div class="item">
              <h3>Supervisor</h3>
              <p>Status: {status.status if status else 'unknown'}</p>
              <p>Current: {status.current_work_id if status else 'n/a'}</p>
              <p>Queue: {status.queue_depth if status else 'n/a'}</p>
              <p>Last action: {status.last_action if status else 'n/a'}</p>
            </div>
            <div class="item">
              <h3>Queue</h3>
              <p>Ready work items: {len(work_items)}</p>
              <ul>
                {''.join(f"<li>{item.work_id} ({item.priority})</li>" for item in work_items[:5])}
              </ul>
            </div>
          </div>
        </div>
        """
        return _render_page("Admin", body)

    return app
