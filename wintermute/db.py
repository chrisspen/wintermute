"""Database layer for Wintermute using Django ORM.

This module provides backwards-compatible aliases and a Database class
that wraps Django ORM operations for the rest of the codebase.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator, Optional

import django
# Ensure Django settings are configured before importing models
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from django.db import models
from asgiref.sync import sync_to_async

# Import Django models
from wintermute.models import (
    Agent,
    AgentMetricsLog,
    AgentResponse,
    AgentSession,
    AgentWake,
    Channel,
    Comment,
    Credential,
    IssueSource,
    MetricDefinition,
    Project,
    RemoteToken,
    RepoResource,
    SessionFile,
    SessionFileConfig,
    SessionFileDefinition,
    Sprint,
    SupervisorState,
    TaskSource,
    Ticket,
    TicketHistory,
    TicketSprint,
    User,
    VMTarget,
    WorkItem,
    WorkItemRun,
)

# Backwards-compatible aliases - use Django models directly
# The old code used frozen dataclasses, but Django models work similarly
AgentRecord = Agent
AgentMetricsLogRecord = AgentMetricsLog
AgentResponseRecord = AgentResponse
AgentSessionRecord = AgentSession
AgentWakeRecord = AgentWake
ChannelRecord = Channel
CommentRecord = Comment
CredentialRecord = Credential
IssueSourceRecord = IssueSource
MetricDefinitionRecord = MetricDefinition
ProjectRecord = Project
RemoteTokenRecord = RemoteToken
RepoResourceRecord = RepoResource
SessionFileRecord = SessionFile
SessionFileConfigRecord = SessionFileConfig
SessionFileDefinitionRecord = SessionFileDefinition
SprintRecord = Sprint
SupervisorStateRecord = SupervisorState
TaskSourceRecord = TaskSource
TicketRecord = Ticket
TicketHistoryRecord = TicketHistory
TicketSprintRecord = TicketSprint
UserRecord = User
VMTargetRecord = VMTarget
WorkItemRecord = WorkItem
WorkItemRunRecord = WorkItemRun

# Utility functions
DEFAULT_DB_PATH_ENV = "WINTERMUTE_DB"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def json_loads(value: Optional[str]) -> Any:
    if not value:
        return {}
    return json.loads(value)


def _split_repo(repo: str) -> tuple[str, str]:
    cleaned = (repo or "").strip().strip("/")
    if not cleaned:
        return "", ""
    if "/" not in cleaned:
        return cleaned, cleaned
    owner, name = cleaned.split("/", 1)
    return owner, name


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Database:
    """Wrapper around Django ORM for backwards compatibility."""

    def __init__(self, db_path: Optional[str] = None):
        """Initialize database connection.

        Note: db_path is ignored since Django manages the connection.
        """
        self.db_path = db_path or os.environ.get(DEFAULT_DB_PATH_ENV, "")

    @contextmanager
    def session(self) -> Generator[Any, None, None]:
        """Context manager for database session.

        With Django, transactions are automatic, but we provide this
        for backwards compatibility.
        """
        from django.db import transaction
        with transaction.atomic():
            yield self

    # ============ Task Sources ============
    def get_task_source(self, source_id: str) -> Optional[TaskSource]:
        try:
            return TaskSource.objects.get(id=source_id)
        except TaskSource.DoesNotExist:
            return None

    def list_task_sources(self) -> list[TaskSource]:
        return list(TaskSource.objects.all())

    def upsert_task_source(
        self,
        source_id: str,
        enabled: bool,
        base_priority: int,
        poll_interval_seconds: int,
        config: dict[str, Any],
    ) -> TaskSource:
        now = utc_now()
        obj, created = TaskSource.objects.update_or_create(
            id=source_id,
            defaults={
                "enabled": 1 if enabled else 0,
                "base_priority": base_priority,
                "poll_interval_seconds": poll_interval_seconds,
                "config_json": json_dumps(config),
                "updated_at": now,
            }
        )
        if created:
            obj.created_at = now
            obj.save()
        return obj

    # ============ Work Items ============
    def get_work_item(self, work_id: str) -> Optional[WorkItem]:
        try:
            return WorkItem.objects.get(work_id=work_id)
        except WorkItem.DoesNotExist:
            return None

    def list_work_items(self, status: Optional[str] = None) -> list[WorkItem]:
        qs = WorkItem.objects.all()
        if status:
            qs = qs.filter(status=status)
        return list(qs)

    def upsert_work_item(
        self,
        work_id: str,
        source_id: str,
        priority: int,
        status: str,
        checkpoint: dict[str, Any],
        run_after: str = "",
        attempts: int = 0,
        last_error: Optional[str] = None,
        last_traceback: Optional[str] = None,
    ) -> WorkItem:
        now = utc_now()
        obj, created = WorkItem.objects.update_or_create(
            work_id=work_id,
            defaults={
                "source_id": source_id,
                "priority": priority,
                "status": status,
                "checkpoint_json": json_dumps(checkpoint),
                "run_after": run_after or "",
                "attempts": attempts,
                "last_error": last_error,
                "last_traceback": last_traceback,
                "updated_at": now,
            }
        )
        if created:
            obj.created_at = now
            obj.save()
        return obj

    def delete_work_item(self, work_id: str) -> bool:
        deleted, _ = WorkItem.objects.filter(work_id=work_id).delete()
        return deleted > 0

    # ============ Supervisor State ============
    def get_supervisor_state(self) -> Optional[SupervisorState]:
        return SupervisorState.objects.first()

    def upsert_supervisor_state(
        self,
        status: str,
        current_work_id: Optional[str] = None,
        last_action: Optional[str] = None,
        queue_depth: int = 0,
    ) -> SupervisorState:
        now = utc_now()
        obj = SupervisorState.objects.first()
        if obj:
            obj.status = status
            obj.current_work_id = current_work_id
            obj.last_action = last_action
            obj.queue_depth = queue_depth
            obj.updated_at = now
            obj.save()
        else:
            obj = SupervisorState.objects.create(
                id=generate_uuid(),
                status=status,
                current_work_id=current_work_id,
                last_action=last_action,
                queue_depth=queue_depth,
                updated_at=now,
            )
        return obj

    # ============ Projects ============
    def get_project(self, project_id: str) -> Optional[Project]:
        try:
            return Project.objects.get(id=project_id)
        except Project.DoesNotExist:
            return None

    def get_project_by_slug(self, slug: str) -> Optional[Project]:
        try:
            return Project.objects.get(slug=slug)
        except Project.DoesNotExist:
            return None

    def list_projects(self) -> list[Project]:
        return list(Project.objects.all())

    # ============ VM Targets ============
    def get_vm_target(self, vm_id: str) -> Optional[VMTarget]:
        try:
            return VMTarget.objects.get(id=vm_id)
        except VMTarget.DoesNotExist:
            return None

    def list_vm_targets(self) -> list[VMTarget]:
        return list(VMTarget.objects.all())

    # ============ Agents ============
    def get_agent(self, agent_id: str) -> Optional[Agent]:
        try:
            return Agent.objects.get(id=agent_id)
        except Agent.DoesNotExist:
            return None

    def get_agent_by_slug(self, slug: str) -> Optional[Agent]:
        try:
            return Agent.objects.get(slug=slug)
        except Agent.DoesNotExist:
            return None

    def list_agents(self) -> list[Agent]:
        return list(Agent.objects.all())

    # ============ Agent Sessions ============
    def get_agent_session(self, session_id: str) -> Optional[AgentSession]:
        try:
            return AgentSession.objects.get(id=session_id)
        except AgentSession.DoesNotExist:
            return None

    def list_agent_sessions(self, status: Optional[str] = None, project_id: Optional[str] = None) -> list[AgentSession]:
        qs = AgentSession.objects.all()
        if status:
            qs = qs.filter(status=status)
        if project_id:
            qs = qs.filter(project_id=project_id)
        return list(qs)

    def create_agent_session(
        self,
        agent_id: str,
        project_id: Optional[str],
        ticket_id: Optional[str],
        status: str,
        repo_path: str,
        thread_ts: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AgentSession:
        now = utc_now()
        return AgentSession.objects.create(
            id=session_id or generate_uuid(),
            agent_id=agent_id,
            project_id=project_id,
            ticket_id=ticket_id,
            status=status,
            repo_path=repo_path,
            thread_ts=thread_ts,
            initial_prompt=initial_prompt,
            last_output_offset=0,
            awaiting_response=0,
            awaiting_response_offset=0,
            created_at=now,
            updated_at=now,
        )

    def update_agent_session(self, session_id: str, **kwargs) -> bool:
        kwargs["updated_at"] = utc_now()
        updated = AgentSession.objects.filter(id=session_id).update(**kwargs)
        return updated > 0

    def delete_agent_session(self, session_id: str) -> bool:
        deleted, _ = AgentSession.objects.filter(id=session_id).delete()
        return deleted > 0

    # ============ Tickets ============
    def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        try:
            return Ticket.objects.get(id=ticket_id)
        except Ticket.DoesNotExist:
            return None

    def get_ticket_by_source_url(self, source_url: str) -> Optional[Ticket]:
        try:
            return Ticket.objects.get(source_url=source_url)
        except Ticket.DoesNotExist:
            return None

    def list_tickets(self, project_id: Optional[str] = None, status: Optional[str] = None) -> list[Ticket]:
        qs = Ticket.objects.all()
        if project_id:
            qs = qs.filter(project_id=project_id)
        if status:
            qs = qs.filter(status=status)
        return list(qs)

    def create_ticket(
        self,
        project_id: str,
        title: str,
        status: str,
        description: Optional[str] = None,
        source_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        auto_start: int = 0,
        created_by_id: Optional[str] = None,
    ) -> Ticket:
        now = utc_now()
        # Get next count for this project
        from django.db.models import Max
        max_count = Ticket.objects.filter(project_id=project_id).aggregate(Max('count'))['count__max']
        next_count = (max_count or 0) + 1

        return Ticket.objects.create(
            id=generate_uuid(),
            project_id=project_id,
            title=title,
            status=status,
            description=description,
            source_url=source_url,
            agent_id=agent_id,
            auto_start=auto_start,
            count=next_count,
            created_by_id=created_by_id,
            created_at=now,
            updated_at=now,
        )

    def update_ticket(self, ticket_id: str, **kwargs) -> bool:
        kwargs["updated_at"] = utc_now()
        updated = Ticket.objects.filter(id=ticket_id).update(**kwargs)
        return updated > 0

    # ============ Comments ============
    def get_comment(self, comment_id: str) -> Optional[Comment]:
        try:
            return Comment.objects.get(id=comment_id)
        except Comment.DoesNotExist:
            return None

    def list_comments(self, ticket_id: Optional[str] = None, session_id: Optional[str] = None) -> list[Comment]:
        qs = Comment.objects.all()
        if ticket_id:
            qs = qs.filter(ticket_id=ticket_id)
        if session_id:
            qs = qs.filter(session_id=session_id)
        return list(qs)

    def create_comment(
        self,
        body: str,
        public: int = 0,
        approved: int = 0,
        sent: int = 0,
        ticket_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        author: Optional[str] = None,
        source_id: Optional[str] = None,
        issue_number: Optional[int] = None,
        origin: Optional[str] = None,
    ) -> Comment:
        now = utc_now()
        return Comment.objects.create(
            id=generate_uuid(),
            body=body,
            public=public,
            approved=approved,
            sent=sent,
            ticket_id=ticket_id,
            session_id=session_id,
            project_id=project_id,
            agent_id=agent_id,
            agent_session_id=agent_session_id,
            author=author,
            source_id=source_id,
            issue_number=issue_number,
            origin=origin,
            created_at=now,
        )

    def insert_comment(
        self,
        comment_id: str,
        body: str,
        public: bool = False,
        approved: bool = False,
        sent: bool = False,
        ticket_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        author: Optional[str] = None,
        source_id: Optional[str] = None,
        issue_number: Optional[int] = None,
        origin: Optional[str] = None,
    ) -> Comment:
        """Alias for create_comment with explicit comment_id and bool parameters."""
        now = utc_now()
        return Comment.objects.create(
            id=comment_id,
            body=body,
            public=1 if public else 0,
            approved=1 if approved else 0,
            sent=1 if sent else 0,
            ticket_id=ticket_id,
            session_id=session_id,
            project_id=project_id,
            agent_id=agent_id,
            agent_session_id=agent_session_id,
            author=author,
            source_id=source_id,
            issue_number=issue_number,
            origin=origin,
            created_at=now,
        )

    def update_comment(self, comment_id: str, **kwargs) -> bool:
        updated = Comment.objects.filter(id=comment_id).update(**kwargs)
        return updated > 0

    def list_pending_comments(self) -> list[Comment]:
        """List comments that are public and approved but not yet sent."""
        return list(Comment.objects.filter(public=1, approved=1, sent=0))

    def mark_comment_sent(self, comment_id: str) -> bool:
        """Mark a comment as sent."""
        updated = Comment.objects.filter(id=comment_id).update(sent=1, sent_at=utc_now())
        return updated > 0

    # ============ Issue Sources ============
    def list_issue_sources(self, enabled: Optional[bool] = None) -> list[IssueSource]:
        qs = IssueSource.objects.all()
        if enabled is not None:
            qs = qs.filter(enabled=1 if enabled else 0)
        return list(qs)

    def get_issue_source(self, source_id: str) -> Optional[IssueSource]:
        try:
            return IssueSource.objects.get(id=source_id)
        except IssueSource.DoesNotExist:
            return None

    def get_github_source(self, source_id: str) -> Optional[IssueSource]:
        """Get a GitHub issue source by ID."""
        try:
            return IssueSource.objects.get(id=source_id, provider='github')
        except IssueSource.DoesNotExist:
            return None

    def get_gitlab_source(self, source_id: str) -> Optional[IssueSource]:
        """Get a GitLab issue source by ID."""
        try:
            return IssueSource.objects.get(id=source_id, provider='gitlab')
        except IssueSource.DoesNotExist:
            return None

    def list_github_sources(self, enabled: Optional[bool] = None) -> list[IssueSource]:
        """List GitHub issue sources."""
        qs = IssueSource.objects.filter(provider='github')
        if enabled is not None:
            qs = qs.filter(enabled=1 if enabled else 0)
        return list(qs)

    def list_gitlab_sources(self, enabled: Optional[bool] = None) -> list[IssueSource]:
        """List GitLab issue sources."""
        qs = IssueSource.objects.filter(provider='gitlab')
        if enabled is not None:
            qs = qs.filter(enabled=1 if enabled else 0)
        return list(qs)

    # ============ Remote Tokens ============
    def list_remote_tokens(self) -> list[RemoteToken]:
        return list(RemoteToken.objects.all())

    def get_remote_token(self, token_id: str) -> Optional[RemoteToken]:
        try:
            return RemoteToken.objects.get(id=token_id)
        except RemoteToken.DoesNotExist:
            return None

    def get_github_token(self, token_id: str) -> Optional[RemoteToken]:
        """Get a GitHub token by ID."""
        try:
            return RemoteToken.objects.get(id=token_id, provider='github')
        except RemoteToken.DoesNotExist:
            return None

    def get_gitlab_token(self, token_id: str) -> Optional[RemoteToken]:
        """Get a GitLab token by ID."""
        try:
            return RemoteToken.objects.get(id=token_id, provider='gitlab')
        except RemoteToken.DoesNotExist:
            return None

    # ============ Agent Wakes ============
    def list_agent_wakes(self, session_id: Optional[str] = None, pending_only: bool = False) -> list[AgentWake]:
        qs = AgentWake.objects.all()
        if session_id:
            qs = qs.filter(agent_session_id=session_id)
        if pending_only:
            qs = qs.filter(status="pending")
        return list(qs)

    def get_agent_wake(self, wake_id: str) -> Optional[AgentWake]:
        try:
            return AgentWake.objects.get(id=wake_id)
        except AgentWake.DoesNotExist:
            return None

    def get_pending_agent_wakes(self, before: str) -> list[AgentWake]:
        """Get all pending wakes that should fire before the given time."""
        return list(AgentWake.objects.filter(status="pending", wake_at__lte=before))

    def create_agent_wake(
        self,
        session_id: str,
        wake_at: str,
        duration_seconds: int = 0,
        context: Optional[str] = None,
    ) -> AgentWake:
        now = utc_now()
        return AgentWake.objects.create(
            id=generate_uuid(),
            agent_session_id=session_id,
            wake_at=wake_at,
            duration_seconds=duration_seconds,
            context=context,
            status="pending",
            created_at=now,
            updated_at=now,
        )

    def update_agent_wake(self, wake_id: str, **kwargs) -> bool:
        kwargs["updated_at"] = utc_now()
        updated = AgentWake.objects.filter(id=wake_id).update(**kwargs)
        return updated > 0

    def cancel_agent_wake(self, wake_id: str, cancelled_by: str) -> bool:
        """Cancel an agent wake."""
        now = utc_now()
        updated = AgentWake.objects.filter(id=wake_id, status="pending").update(
            status="cancelled",
            cancelled_at=now,
            cancelled_by=cancelled_by,
            updated_at=now,
        )
        return updated > 0

    def fire_agent_wake(self, wake_id: str) -> bool:
        """Mark an agent wake as fired."""
        now = utc_now()
        updated = AgentWake.objects.filter(id=wake_id, status="pending").update(
            status="fired",
            fired_at=now,
            updated_at=now,
        )
        return updated > 0

    # ============ Channels ============
    def get_channel(self, channel_id: str) -> Optional[Channel]:
        try:
            return Channel.objects.get(channel_id=channel_id)
        except Channel.DoesNotExist:
            return None

    def list_channels(self, agent_id: Optional[str] = None) -> list[Channel]:
        qs = Channel.objects.all()
        if agent_id:
            qs = qs.filter(agent_id=agent_id)
        return list(qs)

    # ============ Sprints ============
    def get_sprint(self, sprint_id: str) -> Optional[Sprint]:
        try:
            return Sprint.objects.get(id=sprint_id)
        except Sprint.DoesNotExist:
            return None

    # ============ Repo Resources ============
    def list_repo_resources(self, project_id: Optional[str] = None, status: Optional[str] = None) -> list[RepoResource]:
        qs = RepoResource.objects.all()
        if project_id:
            qs = qs.filter(project_id=project_id)
        if status:
            qs = qs.filter(status=status)
        return list(qs)

    def get_repo_resource(self, resource_id: str) -> Optional[RepoResource]:
        try:
            return RepoResource.objects.get(id=resource_id)
        except RepoResource.DoesNotExist:
            return None

    def create_repo_resource(
        self,
        project_id: str,
        repo_mode: str,
        path: str,
        status: str,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> RepoResource:
        now = utc_now()
        return RepoResource.objects.create(
            id=generate_uuid(),
            project_id=project_id,
            repo_mode=repo_mode,
            path=path,
            status=status,
            agent_id=agent_id,
            session_id=session_id,
            created_at=now,
            updated_at=now,
        )

    def update_repo_resource(self, resource_id: str, **kwargs) -> bool:
        kwargs["updated_at"] = utc_now()
        updated = RepoResource.objects.filter(id=resource_id).update(**kwargs)
        return updated > 0

    def acquire_repo_resource(
        self,
        project: Any,
        session_id: str,
        agent_id: str,
    ) -> tuple[Optional[RepoResource], Optional[str]]:
        """Acquire an available repo resource for a session.

        Returns (resource, error_message). If no available resource is found,
        returns (None, error_message).
        """
        from django.db import transaction

        now = utc_now()
        with transaction.atomic():
            # Try to find an available resource for this project
            resource = (RepoResource.objects.select_for_update().filter(project_id=project.id, status="available").first())
            if not resource:
                return None, "No available repo resources for this project"

            # Mark it as in use
            resource.status = "in_use"
            resource.session_id = session_id
            resource.agent_id = agent_id
            resource.last_used_at = now
            resource.updated_at = now
            resource.save()

        return resource, None

    def release_repo_resource_for_session(self, session_id: str) -> bool:
        """Release any repo resources associated with a session."""
        now = utc_now()
        updated = RepoResource.objects.filter(session_id=session_id).update(
            status="available",
            session_id=None,
            last_used_at=now,
            updated_at=now,
        )
        return updated > 0

    # ============ Metric Definitions ============
    def list_metric_definitions(self, enabled: Optional[bool] = None) -> list[MetricDefinition]:
        qs = MetricDefinition.objects.all()
        if enabled is not None:
            qs = qs.filter(enabled=1 if enabled else 0)
        return list(qs)

    def get_metric_definition(self, metric_id: str) -> Optional[MetricDefinition]:
        try:
            return MetricDefinition.objects.get(id=metric_id)
        except MetricDefinition.DoesNotExist:
            return None

    def get_metric_definition_by_type(self, metric_type: str) -> Optional[MetricDefinition]:
        try:
            return MetricDefinition.objects.get(metric_type=metric_type)
        except MetricDefinition.DoesNotExist:
            return None

    # ============ Agent Metrics Logs ============
    def create_agent_metrics_log(
        self,
        agent_id: str,
        metric_definition_id: str,
        value: float,
        recorded_at: str,
    ) -> AgentMetricsLog:
        now = utc_now()
        return AgentMetricsLog.objects.create(
            id=generate_uuid(),
            agent_id=agent_id,
            metric_definition_id=metric_definition_id,
            value=value,
            recorded_at=recorded_at,
            created_at=now,
        )

    # ============ Agent Responses ============
    def list_agent_responses(self, agent_id: Optional[str] = None) -> list[AgentResponse]:
        qs = AgentResponse.objects.all()
        if agent_id:
            qs = qs.filter(agent_id=agent_id)
        return list(qs)

    # ============ Session Files ============
    def get_session_file_config(self, config_id: str) -> Optional[SessionFileConfig]:
        try:
            return SessionFileConfig.objects.get(id=config_id)
        except SessionFileConfig.DoesNotExist:
            return None

    def list_session_file_definitions(self, config_id: str) -> list[SessionFileDefinition]:
        return list(SessionFileDefinition.objects.filter(config_id=config_id))

    def get_session_file(self, file_id: str) -> Optional[SessionFile]:
        try:
            return SessionFile.objects.get(id=file_id)
        except SessionFile.DoesNotExist:
            return None

    def list_session_files(self, session_id: str) -> list[SessionFile]:
        return list(SessionFile.objects.filter(session_id=session_id))

    def create_session_file(
        self,
        session_id: str,
        definition_id: str,
        content: str,
    ) -> SessionFile:
        now = utc_now()
        return SessionFile.objects.create(
            id=generate_uuid(),
            session_id=session_id,
            definition_id=definition_id,
            content=content,
            created_at=now,
            updated_at=now,
        )

    def update_session_file(self, file_id: str, **kwargs) -> bool:
        kwargs["updated_at"] = utc_now()
        updated = SessionFile.objects.filter(id=file_id).update(**kwargs)
        return updated > 0

    # ============ Credentials ============
    def get_credential(self, cred_id: str) -> Optional[Credential]:
        try:
            return Credential.objects.get(id=cred_id)
        except Credential.DoesNotExist:
            return None

    def list_credentials(self) -> list[Credential]:
        return list(Credential.objects.all())

    # ============ Users ============
    def get_user(self, user_id: str) -> Optional[User]:
        try:
            return User.objects.get(id=user_id)
        except User.DoesNotExist:
            return None

    def get_user_by_username(self, username: str) -> Optional[User]:
        try:
            return User.objects.get(username=username)
        except User.DoesNotExist:
            return None

    # ============ Supervisor Methods ============
    def initialize(self) -> None:
        """Initialize database. With Django, this is a no-op since migrations handle schema."""
        pass

    def insert_work_item_if_absent(
        self,
        work_id: str,
        source_id: str,
        priority: int,
        checkpoint: dict[str, Any],
    ) -> bool:
        """Insert a work item if it doesn't already exist. Returns True if inserted."""
        from django.db import IntegrityError
        now = utc_now()
        try:
            WorkItem.objects.create(
                work_id=work_id,
                source_id=source_id,
                priority=priority,
                status="queued",
                checkpoint_json=json_dumps(checkpoint),
                run_after="",
                attempts=0,
                created_at=now,
                updated_at=now,
            )
            return True
        except IntegrityError:
            return False

    def fetch_ready_work_items(self, now_iso: str) -> list[WorkItem]:
        """Fetch work items that are queued and ready to run (run_after <= now)."""
        return list(
            WorkItem.objects.filter(status="queued").filter(models.Q(run_after="") | models.Q(run_after__lte=now_iso)).order_by("priority", "run_after")
        )

    def record_run_start(self, work_id: str) -> int:
        """Record the start of a work item run. Returns run_id."""
        now = utc_now()
        run = WorkItemRun.objects.create(
            work_id=work_id,
            started_at=now,
            status="running",
        )
        return run.run_id

    def record_run_end(self, run_id: int, status: str, error: Optional[str] = None) -> None:
        """Record the end of a work item run."""
        WorkItemRun.objects.filter(run_id=run_id).update(
            ended_at=utc_now(),
            status=status,
            error=error,
        )

    def update_work_item_status(
        self,
        work_id: str,
        status: str,
        last_error: Optional[str] = None,
        last_traceback: Optional[str] = None,
        checkpoint: Optional[dict[str, Any]] = None,
        run_after: Optional[str] = None,
        attempts: Optional[int] = None,
        clear_errors: bool = False,
    ) -> bool:
        """Update work item status with optional additional fields."""
        updates: dict[str, Any] = {
            "status": status,
            "updated_at": utc_now(),
        }
        if last_error is not None:
            updates["last_error"] = last_error
        if last_traceback is not None:
            updates["last_traceback"] = last_traceback
        if checkpoint is not None:
            updates["checkpoint"] = json_dumps(checkpoint)
        if run_after is not None:
            updates["run_after"] = run_after
        if attempts is not None:
            updates["attempts"] = attempts
        if clear_errors:
            updates["last_error"] = None
            updates["last_traceback"] = None
        updated = WorkItem.objects.filter(work_id=work_id).update(**updates)
        return updated > 0

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        """Alias for get_agent_session for backwards compatibility."""
        return self.get_agent_session(session_id)

    def list_sessions(self, status: Optional[str] = None, project_id: Optional[str] = None) -> list[AgentSession]:
        """Alias for list_agent_sessions for backwards compatibility."""
        return self.list_agent_sessions(status, project_id)

    def update_session(self, session_id: str, **kwargs) -> bool:
        """Alias for update_agent_session for backwards compatibility."""
        return self.update_agent_session(session_id, **kwargs)

    def insert_session(
        self,
        session_id: str,
        project_id: str,
        agent_id: str,
        ticket_id: Optional[str] = None,
        status: str = "running",
        repo_path: Optional[str] = None,
        thread_ts: Optional[str] = None,
    ) -> AgentSession:
        """Alias for create_agent_session for backwards compatibility."""
        return self.create_agent_session(
            agent_id=agent_id,
            project_id=project_id,
            ticket_id=ticket_id,
            session_id=session_id,
            status=status,
            repo_path=repo_path,
            thread_ts=thread_ts,
        )

    def update_supervisor_state(
        self,
        status: str,
        current_work_id: Optional[str] = None,
        last_action: Optional[str] = None,
        queue_depth: int = 0,
    ) -> SupervisorState:
        """Update or create supervisor state. Returns the state object."""
        return self.upsert_supervisor_state(status, current_work_id, last_action, queue_depth)

    # ============ Credential Methods ============
    def get_credential_by_name(self, provider: str, name: str) -> Optional[Credential]:
        """Get credential by provider and name."""
        try:
            return Credential.objects.get(provider=provider, name=name)
        except Credential.DoesNotExist:
            return None

    def get_latest_credential_update(self) -> Optional[str]:
        """Get the latest created_at from credentials (Credential model has no updated_at)."""
        from django.db.models import Max
        result = Credential.objects.aggregate(Max('created_at'))
        return result.get('created_at__max')

    def get_latest_github_token_update(self) -> Optional[str]:
        """Get the latest updated_at from GitHub tokens (RemoteToken with provider='github')."""
        from django.db.models import Max
        result = RemoteToken.objects.filter(provider='github').aggregate(Max('updated_at'))
        return result.get('updated_at__max')

    def get_latest_gitlab_token_update(self) -> Optional[str]:
        """Get the latest updated_at from GitLab tokens (RemoteToken with provider='gitlab')."""
        from django.db.models import Max
        result = RemoteToken.objects.filter(provider='gitlab').aggregate(Max('updated_at'))
        return result.get('updated_at__max')

    def list_github_tokens(self) -> list[RemoteToken]:
        """List all GitHub tokens."""
        return list(RemoteToken.objects.filter(provider='github'))

    def list_gitlab_tokens(self) -> list[RemoteToken]:
        """List all GitLab tokens."""
        return list(RemoteToken.objects.filter(provider='gitlab'))

    # ============ Sprint Methods ============
    def insert_sprint(
        self,
        sprint_id: str,
        name: str,
        start_date: str,
        end_date: str,
        enabled: bool,
        status: str,
    ) -> Sprint:
        """Insert a new sprint."""
        now = utc_now()
        return Sprint.objects.create(
            id=sprint_id,
            name=name,
            start_date=start_date,
            end_date=end_date,
            enabled=1 if enabled else 0,
            status=status,
            created_at=now,
            updated_at=now,
        )

    def update_sprint(self, sprint_id: str, **kwargs) -> bool:
        """Update a sprint."""
        kwargs["updated_at"] = utc_now()
        updated = Sprint.objects.filter(id=sprint_id).update(**kwargs)
        return updated > 0

    def list_sprints(self, status: Optional[str] = None, enabled: Optional[bool] = None) -> list[Sprint]:
        """List sprints with optional filters."""
        qs = Sprint.objects.all()
        if status is not None:
            qs = qs.filter(status=status)
        if enabled is not None:
            qs = qs.filter(enabled=1 if enabled else 0)
        return list(qs)

    def move_open_tickets_to_sprint(self, old_sprint_id: str, new_sprint_id: str) -> int:
        """Move open tickets from old sprint to new sprint. Returns count moved."""
        # Get ticket_sprint entries for old sprint
        old_entries = TicketSprint.objects.filter(sprint_id=old_sprint_id)
        moved = 0
        for entry in old_entries:
            ticket = self.get_ticket(entry.ticket_id)
            if ticket and ticket.status in ("open", "in-progress"):
                # Check if already in new sprint
                if not TicketSprint.objects.filter(ticket_id=entry.ticket_id, sprint_id=new_sprint_id).exists():
                    TicketSprint.objects.create(
                        id=generate_uuid(),
                        ticket_id=entry.ticket_id,
                        sprint_id=new_sprint_id,
                        created_at=utc_now(),
                    )
                    moved += 1
        return moved

    # ============ Repo Resource Cleanup ============
    def list_repo_resources_for_cleanup(self, cutoff_iso: str) -> list[RepoResource]:
        """List repo resources that are available and older than cutoff."""
        return list(RepoResource.objects.filter(status="available", updated_at__lt=cutoff_iso))

    def delete_repo_resource(self, resource_id: str) -> bool:
        """Delete a repo resource."""
        deleted, _ = RepoResource.objects.filter(id=resource_id).delete()
        return deleted > 0

    # ============ Metrics ============
    def insert_agent_metrics_log(
        self,
        log_id: str,
        agent_id: str,
        metric_definition_id: str,
        value: float,
    ) -> AgentMetricsLog:
        """Insert an agent metrics log entry."""
        now = utc_now()
        return AgentMetricsLog.objects.create(
            id=log_id,
            agent_id=agent_id,
            metric_definition_id=metric_definition_id,
            value=value,
            recorded_at=now,
            created_at=now,
        )


# Backwards compatible aliases for deprecated classes
GitHubSourceRecord = IssueSource
GitLabSourceRecord = IssueSource
GitHubTokenRecord = RemoteToken
GitLabTokenRecord = RemoteToken


class AsyncDatabase:
    """Async wrapper around Database for use in async contexts (e.g., supervisor)."""

    def __init__(self, db_path: Optional[str] = None):
        self._sync_db = Database(db_path)
        self.db_path = self._sync_db.db_path

    @property
    def sync_db(self) -> Database:
        """Return the underlying sync Database for use by sources that need sync access."""
        return self._sync_db

    def __getattr__(self, name: str):
        """Auto-wrap any missing method from sync db with sync_to_async."""
        sync_method = getattr(self._sync_db, name, None)
        if sync_method is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        if not callable(sync_method):
            return sync_method
        # Return an async wrapper
        async def async_wrapper(*args, **kwargs):
            return await sync_to_async(sync_method)(*args, **kwargs)

        return async_wrapper

    # Explicitly defined async methods for common operations (for better IDE support)
    async def get_task_source(self, source_id: str) -> Optional[TaskSource]:
        return await sync_to_async(self._sync_db.get_task_source)(source_id)

    async def list_task_sources(self) -> list[TaskSource]:
        return await sync_to_async(self._sync_db.list_task_sources)()

    async def upsert_task_source(self, source_id: str, enabled: bool, base_priority: int, poll_interval_seconds: int, config: dict[str, Any]) -> TaskSource:
        return await sync_to_async(self._sync_db.upsert_task_source)(source_id, enabled, base_priority, poll_interval_seconds, config)

    async def get_work_item(self, work_id: str) -> Optional[WorkItem]:
        return await sync_to_async(self._sync_db.get_work_item)(work_id)

    async def list_work_items(self, status: Optional[str] = None) -> list[WorkItem]:
        return await sync_to_async(self._sync_db.list_work_items)(status)

    async def upsert_work_item(
        self,
        work_id: str,
        source_id: str,
        priority: int,
        status: str,
        checkpoint: dict[str, Any],
        run_after: str = "",
        attempts: int = 0,
        last_error: Optional[str] = None,
        last_traceback: Optional[str] = None
    ) -> WorkItem:
        return await sync_to_async(self._sync_db.upsert_work_item
                                   )(work_id, source_id, priority, status, checkpoint, run_after, attempts, last_error, last_traceback)

    async def update_work_item_status(
        self,
        work_id: str,
        status: str,
        last_error: Optional[str] = None,
        last_traceback: Optional[str] = None,
        checkpoint: Optional[dict[str, Any]] = None,
        run_after: Optional[str] = None,
        attempts: Optional[int] = None,
        clear_errors: bool = False,
    ) -> bool:
        return await sync_to_async(self._sync_db.update_work_item_status
                                   )(work_id, status, last_error, last_traceback, checkpoint, run_after, attempts, clear_errors)

    async def delete_work_item(self, work_id: str) -> bool:
        return await sync_to_async(self._sync_db.delete_work_item)(work_id)

    async def get_supervisor_state(self) -> Optional[SupervisorState]:
        return await sync_to_async(self._sync_db.get_supervisor_state)()

    async def upsert_supervisor_state(
        self, status: str, current_work_id: Optional[str] = None, last_action: Optional[str] = None, queue_depth: int = 0
    ) -> SupervisorState:
        return await sync_to_async(self._sync_db.upsert_supervisor_state)(status, current_work_id, last_action, queue_depth)

    async def get_project(self, project_id: str) -> Optional[Project]:
        return await sync_to_async(self._sync_db.get_project)(project_id)

    async def get_project_by_slug(self, slug: str) -> Optional[Project]:
        return await sync_to_async(self._sync_db.get_project_by_slug)(slug)

    async def list_projects(self) -> list[Project]:
        return await sync_to_async(self._sync_db.list_projects)()

    async def get_vm_target(self, vm_id: str) -> Optional[VMTarget]:
        return await sync_to_async(self._sync_db.get_vm_target)(vm_id)

    async def list_vm_targets(self) -> list[VMTarget]:
        return await sync_to_async(self._sync_db.list_vm_targets)()

    async def get_agent(self, agent_id: str) -> Optional[Agent]:
        return await sync_to_async(self._sync_db.get_agent)(agent_id)

    async def get_agent_by_slug(self, slug: str) -> Optional[Agent]:
        return await sync_to_async(self._sync_db.get_agent_by_slug)(slug)

    async def list_agents(self) -> list[Agent]:
        return await sync_to_async(self._sync_db.list_agents)()

    async def get_agent_session(self, session_id: str) -> Optional[AgentSession]:
        return await sync_to_async(self._sync_db.get_agent_session)(session_id)

    async def list_agent_sessions(self, status: Optional[str] = None, project_id: Optional[str] = None) -> list[AgentSession]:
        return await sync_to_async(self._sync_db.list_agent_sessions)(status, project_id)

    async def create_agent_session(
        self,
        agent_id: str,
        project_id: Optional[str],
        ticket_id: Optional[str],
        status: str,
        repo_path: str,
        thread_ts: Optional[str] = None,
        initial_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AgentSession:
        return await sync_to_async(self._sync_db.create_agent_session
                                   )(agent_id, project_id, ticket_id, status, repo_path, thread_ts, initial_prompt, session_id)

    async def update_agent_session(self, session_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_agent_session)(session_id, **kwargs)

    async def delete_agent_session(self, session_id: str) -> bool:
        return await sync_to_async(self._sync_db.delete_agent_session)(session_id)

    async def get_ticket(self, ticket_id: str) -> Optional[Ticket]:
        return await sync_to_async(self._sync_db.get_ticket)(ticket_id)

    async def get_ticket_by_source_url(self, source_url: str) -> Optional[Ticket]:
        return await sync_to_async(self._sync_db.get_ticket_by_source_url)(source_url)

    async def list_tickets(self, project_id: Optional[str] = None, status: Optional[str] = None) -> list[Ticket]:
        return await sync_to_async(self._sync_db.list_tickets)(project_id, status)

    async def create_ticket(
        self,
        project_id: str,
        title: str,
        status: str,
        description: Optional[str] = None,
        source_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        auto_start: int = 0,
        created_by_id: Optional[str] = None
    ) -> Ticket:
        return await sync_to_async(self._sync_db.create_ticket)(project_id, title, status, description, source_url, agent_id, auto_start, created_by_id)

    async def update_ticket(self, ticket_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_ticket)(ticket_id, **kwargs)

    async def get_comment(self, comment_id: str) -> Optional[Comment]:
        return await sync_to_async(self._sync_db.get_comment)(comment_id)

    async def list_comments(self, ticket_id: Optional[str] = None, session_id: Optional[str] = None) -> list[Comment]:
        return await sync_to_async(self._sync_db.list_comments)(ticket_id, session_id)

    async def create_comment(
        self,
        body: str,
        public: int = 0,
        approved: int = 0,
        sent: int = 0,
        ticket_id: Optional[str] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_session_id: Optional[str] = None,
        author: Optional[str] = None,
        source_id: Optional[str] = None,
        issue_number: Optional[int] = None,
        origin: Optional[str] = None
    ) -> Comment:
        return await sync_to_async(
            self._sync_db.create_comment
        )(body, public, approved, sent, ticket_id, session_id, project_id, agent_id, agent_session_id, author, source_id, issue_number, origin)

    async def update_comment(self, comment_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_comment)(comment_id, **kwargs)

    async def list_pending_comments(self) -> list[Comment]:
        return await sync_to_async(self._sync_db.list_pending_comments)()

    async def mark_comment_sent(self, comment_id: str) -> bool:
        return await sync_to_async(self._sync_db.mark_comment_sent)(comment_id)

    async def list_issue_sources(self, enabled: Optional[bool] = None) -> list[IssueSource]:
        return await sync_to_async(self._sync_db.list_issue_sources)(enabled)

    async def get_issue_source(self, source_id: str) -> Optional[IssueSource]:
        return await sync_to_async(self._sync_db.get_issue_source)(source_id)

    async def get_github_source(self, source_id: str) -> Optional[IssueSource]:
        return await sync_to_async(self._sync_db.get_github_source)(source_id)

    async def get_gitlab_source(self, source_id: str) -> Optional[IssueSource]:
        return await sync_to_async(self._sync_db.get_gitlab_source)(source_id)

    async def list_github_sources(self, enabled: Optional[bool] = None) -> list[IssueSource]:
        return await sync_to_async(self._sync_db.list_github_sources)(enabled)

    async def list_gitlab_sources(self, enabled: Optional[bool] = None) -> list[IssueSource]:
        return await sync_to_async(self._sync_db.list_gitlab_sources)(enabled)

    async def list_remote_tokens(self) -> list[RemoteToken]:
        return await sync_to_async(self._sync_db.list_remote_tokens)()

    async def get_remote_token(self, token_id: str) -> Optional[RemoteToken]:
        return await sync_to_async(self._sync_db.get_remote_token)(token_id)

    async def get_github_token(self, token_id: str) -> Optional[RemoteToken]:
        return await sync_to_async(self._sync_db.get_github_token)(token_id)

    async def get_gitlab_token(self, token_id: str) -> Optional[RemoteToken]:
        return await sync_to_async(self._sync_db.get_gitlab_token)(token_id)

    async def list_agent_wakes(self, session_id: Optional[str] = None, pending_only: bool = False) -> list[AgentWake]:
        return await sync_to_async(self._sync_db.list_agent_wakes)(session_id, pending_only)

    async def get_agent_wake(self, wake_id: str) -> Optional[AgentWake]:
        return await sync_to_async(self._sync_db.get_agent_wake)(wake_id)

    async def get_pending_agent_wakes(self, before: str) -> list[AgentWake]:
        return await sync_to_async(self._sync_db.get_pending_agent_wakes)(before)

    async def create_agent_wake(self, session_id: str, wake_at: str, duration_seconds: int = 0, context: Optional[str] = None) -> AgentWake:
        return await sync_to_async(self._sync_db.create_agent_wake)(session_id, wake_at, duration_seconds, context)

    async def update_agent_wake(self, wake_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_agent_wake)(wake_id, **kwargs)

    async def cancel_agent_wake(self, wake_id: str, cancelled_by: str) -> bool:
        return await sync_to_async(self._sync_db.cancel_agent_wake)(wake_id, cancelled_by)

    async def fire_agent_wake(self, wake_id: str) -> bool:
        return await sync_to_async(self._sync_db.fire_agent_wake)(wake_id)

    async def get_channel(self, channel_id: str) -> Optional[Channel]:
        return await sync_to_async(self._sync_db.get_channel)(channel_id)

    async def list_channels(self, agent_id: Optional[str] = None) -> list[Channel]:
        return await sync_to_async(self._sync_db.list_channels)(agent_id)

    async def list_sprints(self, status: Optional[str] = None, enabled: Optional[bool] = None) -> list[Sprint]:
        return await sync_to_async(self._sync_db.list_sprints)(status, enabled)

    async def get_sprint(self, sprint_id: str) -> Optional[Sprint]:
        return await sync_to_async(self._sync_db.get_sprint)(sprint_id)

    async def list_repo_resources(self, project_id: Optional[str] = None, status: Optional[str] = None) -> list[RepoResource]:
        return await sync_to_async(self._sync_db.list_repo_resources)(project_id, status)

    async def get_repo_resource(self, resource_id: str) -> Optional[RepoResource]:
        return await sync_to_async(self._sync_db.get_repo_resource)(resource_id)

    async def create_repo_resource(
        self, project_id: str, repo_mode: str, path: str, status: str, agent_id: Optional[str] = None, session_id: Optional[str] = None
    ) -> RepoResource:
        return await sync_to_async(self._sync_db.create_repo_resource)(project_id, repo_mode, path, status, agent_id, session_id)

    async def update_repo_resource(self, resource_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_repo_resource)(resource_id, **kwargs)

    async def acquire_repo_resource(
        self,
        project: Any,
        session_id: str,
        agent_id: str,
    ) -> tuple[Optional[RepoResource], Optional[str]]:
        return await sync_to_async(self._sync_db.acquire_repo_resource)(project, session_id, agent_id)

    async def release_repo_resource_for_session(self, session_id: str) -> bool:
        return await sync_to_async(self._sync_db.release_repo_resource_for_session)(session_id)

    async def list_metric_definitions(self, enabled: Optional[bool] = None) -> list[MetricDefinition]:
        return await sync_to_async(self._sync_db.list_metric_definitions)(enabled)

    async def get_metric_definition(self, metric_id: str) -> Optional[MetricDefinition]:
        return await sync_to_async(self._sync_db.get_metric_definition)(metric_id)

    async def get_metric_definition_by_type(self, metric_type: str) -> Optional[MetricDefinition]:
        return await sync_to_async(self._sync_db.get_metric_definition_by_type)(metric_type)

    async def create_agent_metrics_log(self, agent_id: str, metric_definition_id: str, value: float, recorded_at: str) -> AgentMetricsLog:
        return await sync_to_async(self._sync_db.create_agent_metrics_log)(agent_id, metric_definition_id, value, recorded_at)

    async def list_agent_responses(self, agent_id: Optional[str] = None) -> list[AgentResponse]:
        return await sync_to_async(self._sync_db.list_agent_responses)(agent_id)

    async def get_session_file_config(self, config_id: str) -> Optional[SessionFileConfig]:
        return await sync_to_async(self._sync_db.get_session_file_config)(config_id)

    async def list_session_file_definitions(self, config_id: str) -> list[SessionFileDefinition]:
        return await sync_to_async(self._sync_db.list_session_file_definitions)(config_id)

    async def get_session_file(self, file_id: str) -> Optional[SessionFile]:
        return await sync_to_async(self._sync_db.get_session_file)(file_id)

    async def list_session_files(self, session_id: str) -> list[SessionFile]:
        return await sync_to_async(self._sync_db.list_session_files)(session_id)

    async def create_session_file(self, session_id: str, definition_id: str, content: str) -> SessionFile:
        return await sync_to_async(self._sync_db.create_session_file)(session_id, definition_id, content)

    async def update_session_file(self, file_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_session_file)(file_id, **kwargs)

    async def get_credential(self, cred_id: str) -> Optional[Credential]:
        return await sync_to_async(self._sync_db.get_credential)(cred_id)

    async def list_credentials(self) -> list[Credential]:
        return await sync_to_async(self._sync_db.list_credentials)()

    async def get_user(self, user_id: str) -> Optional[User]:
        return await sync_to_async(self._sync_db.get_user)(user_id)

    async def get_user_by_username(self, username: str) -> Optional[User]:
        return await sync_to_async(self._sync_db.get_user_by_username)(username)

    # ============ Supervisor Methods (Async) ============
    async def initialize(self) -> None:
        return await sync_to_async(self._sync_db.initialize)()

    async def insert_work_item_if_absent(self, work_id: str, source_id: str, priority: int, checkpoint: dict[str, Any]) -> bool:
        return await sync_to_async(self._sync_db.insert_work_item_if_absent)(work_id, source_id, priority, checkpoint)

    async def fetch_ready_work_items(self, now_iso: str) -> list[WorkItem]:
        return await sync_to_async(self._sync_db.fetch_ready_work_items)(now_iso)

    async def record_run_start(self, work_id: str) -> int:
        return await sync_to_async(self._sync_db.record_run_start)(work_id)

    async def record_run_end(self, run_id: int, status: str, error: Optional[str] = None) -> None:
        return await sync_to_async(self._sync_db.record_run_end)(run_id, status, error)

    async def get_session(self, session_id: str) -> Optional[AgentSession]:
        return await sync_to_async(self._sync_db.get_session)(session_id)

    async def list_sessions(self, status: Optional[str] = None, project_id: Optional[str] = None) -> list[AgentSession]:
        return await sync_to_async(self._sync_db.list_sessions)(status, project_id)

    async def update_session(self, session_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_session)(session_id, **kwargs)

    async def insert_session(
        self,
        session_id: str,
        project_id: str,
        agent_id: str,
        ticket_id: Optional[str] = None,
        status: str = "running",
        repo_path: Optional[str] = None,
        thread_ts: Optional[str] = None,
    ) -> AgentSession:
        return await sync_to_async(self._sync_db.insert_session)(session_id, project_id, agent_id, ticket_id, status, repo_path, thread_ts)

    async def update_supervisor_state(
        self, status: str, current_work_id: Optional[str] = None, last_action: Optional[str] = None, queue_depth: int = 0
    ) -> SupervisorState:
        return await sync_to_async(self._sync_db.update_supervisor_state)(status, current_work_id, last_action, queue_depth)

    # ============ Credential Methods (Async) ============
    async def get_credential_by_name(self, provider: str, name: str) -> Optional[Credential]:
        return await sync_to_async(self._sync_db.get_credential_by_name)(provider, name)

    async def get_latest_credential_update(self) -> Optional[str]:
        return await sync_to_async(self._sync_db.get_latest_credential_update)()

    async def get_latest_github_token_update(self) -> Optional[str]:
        return await sync_to_async(self._sync_db.get_latest_github_token_update)()

    async def get_latest_gitlab_token_update(self) -> Optional[str]:
        return await sync_to_async(self._sync_db.get_latest_gitlab_token_update)()

    async def list_github_tokens(self) -> list[RemoteToken]:
        return await sync_to_async(self._sync_db.list_github_tokens)()

    async def list_gitlab_tokens(self) -> list[RemoteToken]:
        return await sync_to_async(self._sync_db.list_gitlab_tokens)()

    # ============ Sprint Methods (Async) ============
    async def insert_sprint(self, sprint_id: str, name: str, start_date: str, end_date: str, enabled: bool, status: str) -> Sprint:
        return await sync_to_async(self._sync_db.insert_sprint)(sprint_id, name, start_date, end_date, enabled, status)

    async def update_sprint(self, sprint_id: str, **kwargs) -> bool:
        return await sync_to_async(self._sync_db.update_sprint)(sprint_id, **kwargs)

    async def move_open_tickets_to_sprint(self, old_sprint_id: str, new_sprint_id: str) -> int:
        return await sync_to_async(self._sync_db.move_open_tickets_to_sprint)(old_sprint_id, new_sprint_id)

    # ============ Repo Resource Cleanup (Async) ============
    async def list_repo_resources_for_cleanup(self, cutoff_iso: str) -> list[RepoResource]:
        return await sync_to_async(self._sync_db.list_repo_resources_for_cleanup)(cutoff_iso)

    async def delete_repo_resource(self, resource_id: str) -> bool:
        return await sync_to_async(self._sync_db.delete_repo_resource)(resource_id)

    # ============ Metrics (Async) ============
    async def insert_agent_metrics_log(self, log_id: str, agent_id: str, metric_definition_id: str, value: float) -> AgentMetricsLog:
        return await sync_to_async(self._sync_db.insert_agent_metrics_log)(log_id, agent_id, metric_definition_id, value)
