"""Daily standup TaskSource."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
import json
import logging

try:
    from zoneinfo import ZoneInfo
except ImportError: # pragma: no cover
    ZoneInfo = None

from wintermute.db import AsyncDatabase, utc_now
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft

DEFAULT_STANDUP_TIME = "09:30"
DEFAULT_STANDUP_TZ = "UTC"


def _parse_hhmm(value: str) -> Optional[tuple[int, int]]:
    raw = (value or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def _load_timezone(name: str) -> timezone:
    if not name:
        return timezone.utc
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(name)
    except Exception:
        return timezone.utc


@dataclass
class StandupWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
        source = await ctx.db.get_task_source(self.source_id)
        config = source.config if source else {}
        standup_channel = str(config.get("channel") or "").strip() or None
        last_run_at = str(config.get("last_run_at") or "").strip() or None
        now_iso = utc_now()
        sessions = await ctx.db.list_sessions(status="running")
        if standup_channel and ctx.tools.get("slack_post_message"):
            summary = _standup_summary_text(len(sessions), last_run_at, now_iso)
            try:
                await ctx.tools.call(
                    "slack_post_message",
                    {
                        "channel": standup_channel,
                        "text": summary,
                    },
                )
            except Exception as exc:
                logger.warning("Standup Slack intro failed: %s", exc)
        for session in sessions:
            project = await ctx.db.get_project(session.project_id)
            ticket = await ctx.db.get_ticket(session.ticket_id) if session.ticket_id else None
            prompt = _standup_prompt(session, project, ticket, last_run_at, now_iso)
            if await _queue_session_prompt(ctx.db, session, prompt):
                logger.info("Queued standup prompt for session %s", session.id)
        if source:
            new_config = dict(config or {})
            new_config["last_run_at"] = now_iso
            await ctx.db.upsert_task_source(
                source.id,
                source.enabled,
                source.base_priority,
                source.poll_interval_seconds,
                new_config,
            )


class StandupSource(TaskSource):
    id = "standup"
    enabled = False
    base_priority = 55
    poll_interval_seconds = 60

    def __init__(self, now_fn: Optional[Callable[[Any], datetime]] = None) -> None:
        self._now_fn = now_fn

    def _now(self, tz: timezone) -> datetime:
        if self._now_fn:
            return self._now_fn(tz)
        return datetime.now(tz)

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: AsyncDatabase = ctx["db"]
        source = await db.get_task_source(self.id)
        if not source or not source.enabled:
            return []
        config = source.config or {}
        time_raw = str(config.get("time") or config.get("standup_time") or DEFAULT_STANDUP_TIME)
        tz_name = str(config.get("timezone") or config.get("tz") or DEFAULT_STANDUP_TZ)
        hm = _parse_hhmm(time_raw) or _parse_hhmm(DEFAULT_STANDUP_TIME)
        if not hm:
            return []
        tz = _load_timezone(tz_name)
        now = self._now(tz)
        scheduled = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
        if now < scheduled:
            return []
        date_key = scheduled.date().isoformat()
        work_id = f"standup:{date_key}"
        return [
            WorkItemDraft(
                work_id=work_id,
                priority=source.base_priority,
                source_id=self.id,
                checkpoint={
                    "standup_date": date_key,
                    "scheduled_at": scheduled.isoformat(),
                    "timezone": tz_name,
                },
            )
        ]

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        return StandupWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            checkpoint=record.checkpoint,
        )


async def _queue_session_prompt(db: AsyncDatabase, session: Any, prompt: str) -> bool:
    raw_queue = session.queued_user_messages or "[]"
    try:
        queue = json.loads(raw_queue)
        if not isinstance(queue, list):
            queue = []
    except json.JSONDecodeError:
        queue = []
    if prompt in queue:
        return False
    queue.append(prompt)
    await db.update_session(session.id, queued_user_messages=json.dumps(queue))
    return True


def _standup_summary_text(count: int, last_run_at: Optional[str], now_iso: str) -> str:
    if last_run_at:
        window = f"{last_run_at} -> {now_iso}"
    else:
        window = f"since the last standup (now {now_iso})"
    return f"Daily standup started. Sessions queued: {count}. Window: {window}"


def _standup_prompt(
    session: Any,
    project: Any,
    ticket: Any,
    last_run_at: Optional[str],
    now_iso: str,
) -> str:
    window = f"{last_run_at} -> {now_iso}" if last_run_at else f"since the last standup (now {now_iso})"
    lines = [
        "Daily standup time.",
        f"Time window: {window}.",
    ]
    if project:
        lines.append(f"Project: {project.name} ({project.slug})")
    if ticket:
        lines.append(f"Ticket: {ticket.title} ({ticket.id})")
        if ticket.source_url:
            lines.append(f"Source: {ticket.source_url}")
    if session.repo_path:
        lines.append(f"Repo path: {session.repo_path}")
    lines.extend([
        "Please reply with lines starting with 'STANDUP:' that cover:",
        "- what you completed in this window",
        "- what you plan to do next",
        "- blockers or questions (use BLOCKER: if you are stuck)",
    ])
    return "\n".join(lines)
