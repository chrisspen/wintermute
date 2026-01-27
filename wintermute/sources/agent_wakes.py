"""Agent wake TaskSource for scheduled agent wake-ups."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional
import json
import logging

from wintermute.db import Database, utc_now
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft


@dataclass
class AgentWakeWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
        wake_id = self.checkpoint.get("wake_id")
        if not wake_id:
            logger.warning("AgentWakeWorkItem missing wake_id")
            return

        wake = ctx.db.get_agent_wake(wake_id)
        if not wake:
            logger.warning("Agent wake %s not found", wake_id)
            return

        if wake.status != "pending":
            logger.info("Agent wake %s already %s, skipping", wake_id, wake.status)
            return

        session = ctx.db.get_session(wake.agent_session_id)
        if not session:
            logger.warning("Session %s not found for wake %s", wake.agent_session_id, wake_id)
            ctx.db.cancel_agent_wake(wake_id, "system")
            return

        if session.status not in ("running", "blocked"):
            logger.info(
                "Session %s status is %s, cancelling wake %s",
                session.id,
                session.status,
                wake_id,
            )
            ctx.db.cancel_agent_wake(wake_id, "system")
            return

        # Build the wake message
        duration_str = _format_duration(wake.duration_seconds)
        if wake.context:
            message = f'[WINTERMUTE] Wake timer expired ({duration_str}). Context: "{wake.context}"'
        else:
            message = f"[WINTERMUTE] Wake timer expired ({duration_str})."

        # Queue the wake message to the session
        if _queue_session_prompt(ctx.db, session, message):
            logger.info("Delivered wake %s to session %s", wake_id, session.id)
        else:
            logger.info("Wake message already queued for session %s", session.id)

        # Mark the wake as fired
        ctx.db.fire_agent_wake(wake_id)


class AgentWakeSource(TaskSource):
    id = "agent_wakes"
    enabled = True
    base_priority = 30 # Higher priority than most tasks to ensure timely delivery
    poll_interval_seconds = 10 # Poll frequently for wake timers

    def __init__(self, now_fn: Optional[Callable[[Any], datetime]] = None) -> None:
        self._now_fn = now_fn

    def _now(self) -> datetime:
        if self._now_fn:
            return self._now_fn(timezone.utc)
        return datetime.now(timezone.utc)

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: Database = ctx["db"]
        now_iso = self._now().isoformat()

        # Get all pending wakes that should fire now
        pending_wakes = db.get_pending_agent_wakes(before=now_iso)

        drafts = []
        for wake in pending_wakes:
            work_id = f"agent_wake:{wake.id}"
            drafts.append(
                WorkItemDraft(
                    work_id=work_id,
                    priority=self.base_priority,
                    source_id=self.id,
                    checkpoint={
                        "wake_id": wake.id,
                        "agent_session_id": wake.agent_session_id,
                        "wake_at": wake.wake_at,
                        "context": wake.context,
                    },
                )
            )

        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        return AgentWakeWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            checkpoint=record.checkpoint,
        )


def _queue_session_prompt(db: Database, session: Any, prompt: str) -> bool:
    """Queue a prompt message to a session."""
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
    db.update_session(session.id, queued_user_messages=json.dumps(queue))
    return True


def _format_duration(seconds: int) -> str:
    """Format duration in human-readable form."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h"
    days = seconds // 86400
    return f"{days}d"
