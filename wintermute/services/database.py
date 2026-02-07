"""Database service functions using Django ORM directly.

These are stateless functions that wrap Django ORM operations.
No global state, no settings manipulation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Max

from wintermute.models import (
    Agent,
    AgentMetricsLog,
    AgentSession,
    Channel,
    Comment,
    Credential,
    MetricDefinition,
    Project,
    RemoteToken,
    RepoResource,
    Sprint,
    SupervisorState,
    TaskSource,
    Ticket,
    VMTarget,
    WorkItem,
    WorkItemRun,
)
from wintermute.utils import utc_now, generate_uuid

# Re-export model types with Record suffix for backwards compatibility
AgentRecord = Agent
AgentSessionRecord = AgentSession
ChannelRecord = Channel
ProjectRecord = Project
TicketRecord = Ticket
VMTargetRecord = VMTarget
WorkItemRecord = WorkItem

# ============ Sync Functions ============


def initialize():
    """No-op for compatibility. Django handles migrations."""
    pass


def list_task_sources() -> list[TaskSource]:
    return list(TaskSource.objects.all())


def get_task_source(source_id: str) -> Optional[TaskSource]:
    try:
        return TaskSource.objects.get(id=source_id)
    except TaskSource.DoesNotExist:
        return None


def get_work_item(work_id: str) -> Optional[WorkItem]:
    try:
        return WorkItem.objects.get(work_id=work_id)
    except WorkItem.DoesNotExist:
        return None


def get_agent(agent_id: str) -> Optional[Agent]:
    try:
        return Agent.objects.get(id=agent_id)
    except Agent.DoesNotExist:
        return None


def get_agent_by_slug(slug: str) -> Optional[Agent]:
    try:
        return Agent.objects.get(slug=slug)
    except Agent.DoesNotExist:
        return None


def list_agents() -> list[Agent]:
    return list(Agent.objects.all())


def get_session(session_id: str) -> Optional[AgentSession]:
    try:
        return AgentSession.objects.get(id=session_id)
    except AgentSession.DoesNotExist:
        return None


def list_sessions(status: Optional[str] = None, project_id: Optional[str] = None) -> list[AgentSession]:
    qs = AgentSession.objects.all()
    if status:
        qs = qs.filter(status=status)
    if project_id:
        qs = qs.filter(project_id=project_id)
    return list(qs)


def get_vm_target(vm_id: str) -> Optional[VMTarget]:
    try:
        return VMTarget.objects.get(id=vm_id)
    except VMTarget.DoesNotExist:
        return None


def list_channels(agent_id: Optional[str] = None) -> list[Channel]:
    qs = Channel.objects.all()
    if agent_id:
        qs = qs.filter(agent_id=agent_id)
    return list(qs)


def get_channel_by_external_id(channel_type: str, external_id: str) -> Optional[Channel]:
    try:
        return Channel.objects.get(type=channel_type, external_channel_id=external_id)
    except Channel.DoesNotExist:
        return None


def get_project(project_id: str) -> Optional[Project]:
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


def get_project_by_slug(slug: str) -> Optional[Project]:
    try:
        return Project.objects.get(slug=slug)
    except Project.DoesNotExist:
        return None


def list_projects() -> list[Project]:
    return list(Project.objects.all())


def get_ticket(ticket_id: str) -> Optional[Ticket]:
    try:
        return Ticket.objects.get(id=ticket_id)
    except Ticket.DoesNotExist:
        return None


def update_ticket(ticket_id: str, **kwargs) -> bool:
    now = utc_now()
    kwargs['updated_at'] = now
    return Ticket.objects.filter(id=ticket_id).update(**kwargs) > 0


def list_auto_start_tickets() -> list[Ticket]:
    return list(Ticket.objects.filter(auto_start=True, status='open'))


def list_remote_tokens(provider: Optional[str] = None) -> list[RemoteToken]:
    qs = RemoteToken.objects.all()
    if provider:
        qs = qs.filter(provider=provider)
    return list(qs)


def get_remote_token(token_id: str) -> Optional[RemoteToken]:
    try:
        return RemoteToken.objects.get(id=token_id)
    except RemoteToken.DoesNotExist:
        return None


def insert_session(
    session_id: str,
    project_id: Optional[str],
    agent_id: str,
    ticket_id: Optional[str],
    status: str,
    repo_path: str,
    thread_ts: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    workspace_path: Optional[str] = None,
) -> AgentSession:
    now = utc_now()
    return AgentSession.objects.create(
        id=session_id,
        project_id=project_id,
        agent_id=agent_id,
        ticket_id=ticket_id,
        status=status,
        repo_path=repo_path,
        thread_ts=thread_ts,
        last_output_offset=0,
        initial_prompt=initial_prompt,
        workspace_path=workspace_path,
        created_at=now,
        updated_at=now,
    )


def update_session(session_id: str, **kwargs) -> bool:
    now = utc_now()
    kwargs['updated_at'] = now
    return AgentSession.objects.filter(id=session_id).update(**kwargs) > 0


def insert_comment(comment_id: str, body: str, public: int, approved: int, sent: int = 0, **kwargs) -> Comment:
    now = utc_now()
    return Comment.objects.create(id=comment_id, body=body, public=public, approved=approved, sent=sent, created_at=now, **kwargs)


def list_comments(
    approved: Optional[int] = None,
    sent: Optional[int] = None,
    public: Optional[int] = None,
) -> list[Comment]:
    qs = Comment.objects.all()
    if approved is not None:
        qs = qs.filter(approved=approved)
    if sent is not None:
        qs = qs.filter(sent=sent)
    if public is not None:
        qs = qs.filter(public=public)
    return list(qs)


def update_comment(comment_id: str, **kwargs) -> bool:
    return Comment.objects.filter(id=comment_id).update(**kwargs) > 0


def acquire_repo_resource(
    project: Project,
    session_id: str,
    agent_id: Optional[str] = None,
) -> tuple[Optional[RepoResource], Optional[str]]:
    """Acquire a repo resource for a session."""
    repo_mode = project.repo_mode or "local"
    now = utc_now()

    if repo_mode == "local":
        # Check for existing available resource
        existing = RepoResource.objects.filter(project_id=project.id, repo_mode="local", status="available").first()
        if existing:
            existing.status = "in_use"
            existing.session_id = session_id
            existing.updated_at = now
            existing.save()
            return existing, None

        # Check if there's an in-use resource with a running session
        in_use = RepoResource.objects.filter(project_id=project.id, repo_mode="local", status="in_use").first()
        if in_use:
            # Check if the session is still running
            try:
                session = AgentSession.objects.get(id=in_use.session_id)
                if session.status == "running":
                    return None, "local repo already in use"
            except AgentSession.DoesNotExist:
                pass
            # Session not running, take over
            in_use.session_id = session_id
            in_use.updated_at = now
            in_use.save()
            return in_use, None

        # Create new resource
        resource = RepoResource.objects.create(
            id=generate_uuid(),
            project_id=project.id,
            agent_id=agent_id,
            session_id=session_id,
            repo_mode="local",
            path=f"local:{project.id}",
            status="in_use",
            created_at=now,
            updated_at=now,
        )
        return resource, None

    elif repo_mode == "mirror":
        if not project.repo_path:
            return None, "mirror path not configured"

        existing = RepoResource.objects.filter(project_id=project.id, repo_mode="mirror", status="available").first()
        if existing:
            existing.status = "in_use"
            existing.session_id = session_id
            existing.updated_at = now
            existing.save()
            return existing, None

        in_use = RepoResource.objects.filter(project_id=project.id, repo_mode="mirror", status="in_use").first()
        if in_use:
            try:
                session = AgentSession.objects.get(id=in_use.session_id)
                if session.status == "running":
                    return None, "mirror repo already in use"
            except AgentSession.DoesNotExist:
                pass
            in_use.session_id = session_id
            in_use.updated_at = now
            in_use.save()
            return in_use, None

        resource = RepoResource.objects.create(
            id=generate_uuid(),
            project_id=project.id,
            agent_id=agent_id,
            session_id=session_id,
            repo_mode="mirror",
            path=project.repo_path,
            status="in_use",
            created_at=now,
            updated_at=now,
        )
        return resource, None

    elif repo_mode == "clone":
        if not project.repo_path:
            return None, "repo path not configured"

        # Check pool limit
        max_resources = project.max_repo_resources or 3
        existing_count = RepoResource.objects.filter(project_id=project.id, repo_mode="clone").count()

        if existing_count >= max_resources:
            # Try to find an available one
            available = RepoResource.objects.filter(project_id=project.id, repo_mode="clone", status="available").first()
            if available:
                available.status = "in_use"
                available.session_id = session_id
                available.updated_at = now
                available.save()
                return available, None
            return None, "clone pool exhausted"

        # Create new clone resource
        clone_path = f"{project.repo_path}-{session_id}"
        resource = RepoResource.objects.create(
            id=generate_uuid(),
            project_id=project.id,
            agent_id=agent_id,
            session_id=session_id,
            repo_mode="clone",
            path=clone_path,
            status="in_use",
            created_at=now,
            updated_at=now,
        )
        return resource, None

    return None, f"unknown repo mode: {repo_mode}"


def release_repo_resource_for_session(session_id: str) -> bool:
    now = utc_now()
    return RepoResource.objects.filter(session_id=session_id).update(status="available", session_id=None, updated_at=now) > 0


def insert_project(project_id, name, slug, slack_channel_id, **kwargs):
    now = utc_now()
    return Project.objects.create(id=project_id, name=name, slug=slug, slack_channel_id=slack_channel_id, created_at=now, updated_at=now, **kwargs)


def insert_agent(agent_id, name, slug, command, session_mode, vm_target_id, **kwargs):
    now = utc_now()
    return Agent.objects.create(
        id=agent_id, name=name, slug=slug, command=command, session_mode=session_mode, vm_target_id=vm_target_id, created_at=now, updated_at=now, **kwargs
    )


def insert_vm_target(vm_id, name, host, user, port, **kwargs):
    now = utc_now()
    return VMTarget.objects.create(id=vm_id, name=name, host=host, user=user, port=port, created_at=now, updated_at=now, **kwargs)


def update_vm_target(vm_id, **kwargs):
    kwargs['updated_at'] = utc_now()
    return VMTarget.objects.filter(id=vm_id).update(**kwargs)


def insert_channel(channel_id, agent_id, channel_type, name, external_channel_id=None, enabled=True):
    now = utc_now()
    return Channel.objects.create(
        id=channel_id,
        agent_id=agent_id,
        type=channel_type,
        name=name,
        external_channel_id=external_channel_id or "",
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def get_channel(channel_id: str):
    try:
        return Channel.objects.get(id=channel_id)
    except Channel.DoesNotExist:
        return None


def update_channel(channel_id, **kwargs):
    kwargs['updated_at'] = utc_now()
    return Channel.objects.filter(id=channel_id).update(**kwargs)


def delete_channel(channel_id):
    return Channel.objects.filter(id=channel_id).delete()


def insert_metric_definition(definition_id, metric_type, recording_frequency_minutes, enabled):
    now = utc_now()
    return MetricDefinition.objects.create(
        id=definition_id,
        metric_type=metric_type,
        recording_frequency_minutes=recording_frequency_minutes,
        enabled=enabled,
        created_at=now,
        updated_at=now,
    )


def get_metric_definition(definition_id):
    try:
        return MetricDefinition.objects.get(id=definition_id)
    except MetricDefinition.DoesNotExist:
        return None


def list_metric_definitions():
    return list(MetricDefinition.objects.all())


def update_metric_definition(definition_id, **kwargs):
    kwargs['updated_at'] = utc_now()
    return MetricDefinition.objects.filter(id=definition_id).update(**kwargs)


def delete_metric_definition(definition_id):
    return MetricDefinition.objects.filter(id=definition_id).delete()


def insert_agent_metrics_log_sync(log_id, agent_id, metric_definition_id, value):
    now = utc_now()
    return AgentMetricsLog.objects.create(
        id=log_id,
        agent_id=agent_id,
        metric_definition_id=metric_definition_id,
        value=value,
        recorded_at=now,
    )


def get_agent_metrics_log(log_id):
    try:
        return AgentMetricsLog.objects.get(id=log_id)
    except AgentMetricsLog.DoesNotExist:
        return None


def list_agent_metrics_logs(agent_id=None, limit=None):
    qs = AgentMetricsLog.objects.all().order_by('-recorded_at')
    if agent_id:
        qs = qs.filter(agent_id=agent_id)
    if limit:
        qs = qs[:limit]
    return list(qs)


def get_agent_average_memory_usage(agent_id):
    from django.db.models import Avg
    memory_defs = MetricDefinition.objects.filter(metric_type="MEMORY_USAGE").values_list('id', flat=True)
    result = AgentMetricsLog.objects.filter(agent_id=agent_id, metric_definition_id__in=memory_defs).aggregate(avg=Avg('value'))
    return result['avg']


def refresh_agent_average_memory_usage(agent_id):
    avg = get_agent_average_memory_usage(agent_id)
    if avg is not None:
        Agent.objects.filter(id=agent_id).update(average_memory_usage_mb=int(avg), updated_at=utc_now())


def insert_remote_token(token_id, provider, token, note=None, user_id=None, user_login=None):
    now = utc_now()
    return RemoteToken.objects.create(
        id=token_id,
        provider=provider,
        token=token,
        note=note or "",
        user_id=user_id or "",
        user_login=user_login or "",
        created_at=now,
        updated_at=now,
    )


def update_remote_token(token_id, **kwargs):
    kwargs['updated_at'] = utc_now()
    return RemoteToken.objects.filter(id=token_id).update(**kwargs)


def delete_remote_token(token_id):
    return RemoteToken.objects.filter(id=token_id).delete()


class GitHubSourceWrapper:
    """Wrapper to expose Project fields with GitHubSource-compatible names."""

    def __init__(self, project):
        self._project = project
        self.id = project.id
        self.project_id = project.id
        self.enabled = bool(project.source_enabled)
        self.token_id = project.source_token_id
        self.agent_id = project.source_agent_id
        self.auto_start = bool(project.auto_start)
        self.poll_interval_seconds = project.poll_interval_seconds
        self.state = project.issue_state or "open"
        # Parse owner/repo from source_repo
        source_repo = project.source_repo or ""
        if "/" in source_repo:
            self.owner, self.repo = source_repo.split("/", 1)
        else:
            self.owner = ""
            self.repo = source_repo
        # Labels
        try:
            self.labels = json.loads(project.issue_labels_json or "[]")
        except (json.JSONDecodeError, TypeError):
            self.labels = []


def list_github_sources():
    projects = Project.objects.filter(provider="github", source_enabled=1)
    return [GitHubSourceWrapper(p) for p in projects]


class GitLabSourceWrapper:
    """Wrapper to expose Project fields with GitLabSource-compatible names."""

    def __init__(self, project):
        self._project = project
        self.id = project.id
        self.project_id = project.id
        self.enabled = bool(project.source_enabled)
        self.token_id = project.source_token_id
        self.agent_id = project.source_agent_id
        self.auto_start = bool(project.auto_start)
        self.poll_interval_seconds = project.poll_interval_seconds
        self.state = project.issue_state or "opened"
        self.gitlab_project_id = project.source_repo or ""
        self.project_path = project.source_repo or "" # GitLab project path
        # Labels
        try:
            self.labels = json.loads(project.issue_labels_json or "[]")
        except (json.JSONDecodeError, TypeError):
            self.labels = []


def list_gitlab_sources():
    projects = Project.objects.filter(provider="gitlab", source_enabled=1)
    return [GitLabSourceWrapper(p) for p in projects]


def get_project_vm_for_project(project_id):
    # Returns project itself since VM mapping is now on Project model
    try:
        return Project.objects.get(id=project_id)
    except Project.DoesNotExist:
        return None


def insert_session_file_config(config_id, name, description=None):
    from wintermute.models import SessionFileConfig
    now = utc_now()
    return SessionFileConfig.objects.create(
        id=config_id,
        name=name,
        description=description or "",
        created_at=now,
        updated_at=now,
    )


def get_session_file_config(config_id):
    from wintermute.models import SessionFileConfig
    try:
        return SessionFileConfig.objects.get(id=config_id)
    except SessionFileConfig.DoesNotExist:
        return None


def list_session_file_configs():
    from wintermute.models import SessionFileConfig
    return list(SessionFileConfig.objects.all())


def update_session_file_config(config_id, **kwargs):
    from wintermute.models import SessionFileConfig
    kwargs['updated_at'] = utc_now()
    return SessionFileConfig.objects.filter(id=config_id).update(**kwargs)


def delete_session_file_config(config_id):
    from wintermute.models import SessionFileConfig
    return SessionFileConfig.objects.filter(id=config_id).delete()


def insert_session_file_definition(definition_id, config_id, filename, default_content, **kwargs):
    from wintermute.models import SessionFileDefinition
    now = utc_now()
    return SessionFileDefinition.objects.create(
        id=definition_id, config_id=config_id, filename=filename, default_content=default_content, created_at=now, updated_at=now, **kwargs
    )


def get_session_file_definition(definition_id):
    from wintermute.models import SessionFileDefinition
    try:
        return SessionFileDefinition.objects.get(id=definition_id)
    except SessionFileDefinition.DoesNotExist:
        return None


def list_session_file_definitions(config_id):
    from wintermute.models import SessionFileDefinition
    return list(SessionFileDefinition.objects.filter(config_id=config_id).order_by('sort_order'))


def update_session_file_definition(definition_id, **kwargs):
    from wintermute.models import SessionFileDefinition
    kwargs['updated_at'] = utc_now()
    return SessionFileDefinition.objects.filter(id=definition_id).update(**kwargs)


def delete_session_file_definition(definition_id):
    from wintermute.models import SessionFileDefinition
    return SessionFileDefinition.objects.filter(id=definition_id).delete()


def insert_session_file(file_id, agent_id, definition_id, content):
    from wintermute.models import SessionFile
    now = utc_now()
    return SessionFile.objects.create(
        id=file_id,
        agent_id=agent_id,
        definition_id=definition_id,
        content=content,
        created_at=now,
        updated_at=now,
    )


def get_session_file(file_id):
    from wintermute.models import SessionFile
    try:
        return SessionFile.objects.get(id=file_id)
    except SessionFile.DoesNotExist:
        return None


def get_session_file_by_definition(agent_id, definition_id):
    from wintermute.models import SessionFile
    try:
        return SessionFile.objects.get(agent_id=agent_id, definition_id=definition_id)
    except SessionFile.DoesNotExist:
        return None


def list_session_files(agent_id):
    from wintermute.models import SessionFile
    return list(SessionFile.objects.filter(agent_id=agent_id))


def update_session_file(file_id, **kwargs):
    from wintermute.models import SessionFile
    kwargs['updated_at'] = utc_now()
    return SessionFile.objects.filter(id=file_id).update(**kwargs)


def delete_session_file(file_id):
    from wintermute.models import SessionFile
    return SessionFile.objects.filter(id=file_id).delete()


def delete_session_files_for_agent(agent_id):
    from wintermute.models import SessionFile
    return SessionFile.objects.filter(agent_id=agent_id).delete()


# ============ Async Functions ============


@sync_to_async
def alist_task_sources() -> list[TaskSource]:
    return list_task_sources()


@sync_to_async
def aget_task_source(source_id: str) -> Optional[TaskSource]:
    return get_task_source(source_id)


@sync_to_async
def aupsert_task_source(
    source_id: str,
    enabled: bool,
    base_priority: int,
    poll_interval_seconds: int,
    config: dict,
) -> TaskSource:
    now = utc_now()
    obj, created = TaskSource.objects.update_or_create(
        id=source_id,
        defaults={
            "enabled": 1 if enabled else 0,
            "base_priority": base_priority,
            "poll_interval_seconds": poll_interval_seconds,
            "config_json": json.dumps(config),
            "updated_at": now,
        }
    )
    if created:
        obj.created_at = now
        obj.save()
    return obj


@sync_to_async
def aget_work_item(work_id: str) -> Optional[WorkItem]:
    return get_work_item(work_id)


@sync_to_async
def aupdate_work_item_status(
    work_id: str,
    status: str,
    checkpoint: Optional[dict] = None,
    run_after: Optional[str] = None,
    attempts: Optional[int] = None,
    last_error: Optional[str] = None,
    traceback: Optional[str] = None,
    clear_errors: bool = False,
) -> bool:
    now = utc_now()
    updates = {"status": status, "updated_at": now}
    if checkpoint is not None:
        updates["checkpoint_json"] = json.dumps(checkpoint)
    if run_after is not None:
        updates["run_after"] = run_after
    if attempts is not None:
        updates["attempts"] = attempts
    if last_error is not None:
        updates["last_error"] = last_error
    if traceback is not None:
        updates["traceback"] = traceback
    if clear_errors:
        updates["last_error"] = None
        updates["traceback"] = None
    return WorkItem.objects.filter(work_id=work_id).update(**updates) > 0


@sync_to_async
def afetch_ready_work_items(now_iso: str) -> list[WorkItem]:
    return list(WorkItem.objects.filter(status="queued", run_after__lte=now_iso).order_by("priority", "created_at"))


@sync_to_async
def ainsert_work_item_if_absent(
    work_id: str,
    source_id: str,
    priority: int,
    checkpoint: dict,
) -> bool:
    if WorkItem.objects.filter(work_id=work_id).exists():
        return False
    now = utc_now()
    WorkItem.objects.create(
        work_id=work_id,
        source_id=source_id,
        priority=priority,
        status="queued",
        checkpoint_json=json.dumps(checkpoint),
        run_after="",
        attempts=0,
        created_at=now,
        updated_at=now,
    )
    return True


@sync_to_async
def arecord_run_start(work_id: str) -> int:
    now = utc_now()
    run = WorkItemRun.objects.create(
        work_id=work_id,
        started_at=now,
        status="running",
    )
    return run.id


@sync_to_async
def arecord_run_end(run_id: int, status: str, error: Optional[str] = None) -> None:
    now = utc_now()
    WorkItemRun.objects.filter(id=run_id).update(
        status=status,
        ended_at=now,
        error=error,
    )


@sync_to_async
def aupdate_supervisor_state(
    status: str,
    current_work_id: Optional[str] = None,
    last_action: Optional[str] = None,
    queue_depth: int = 0,
) -> SupervisorState:
    now = utc_now()
    state, _ = SupervisorState.objects.update_or_create(
        id="singleton",
        defaults={
            "status": status,
            "current_work_id": current_work_id or "",
            "last_action": last_action or "",
            "queue_depth": queue_depth,
            "updated_at": now,
        }
    )
    return state


@sync_to_async
def alist_repo_resources_for_cleanup(cutoff_iso: str) -> list[RepoResource]:
    return list(RepoResource.objects.filter(status="available", repo_mode="clone", updated_at__lt=cutoff_iso))


@sync_to_async
def adelete_repo_resource(resource_id: str) -> bool:
    return RepoResource.objects.filter(id=resource_id).delete()[0] > 0


@sync_to_async
def aget_latest_credential_update() -> Optional[str]:
    latest = Credential.objects.order_by("-updated_at").first()
    return latest.updated_at if latest else None


@sync_to_async
def aget_latest_github_token_update() -> Optional[str]:
    latest = RemoteToken.objects.filter(provider="github").order_by("-updated_at").first()
    return latest.updated_at if latest else None


@sync_to_async
def aget_latest_gitlab_token_update() -> Optional[str]:
    latest = RemoteToken.objects.filter(provider="gitlab").order_by("-updated_at").first()
    return latest.updated_at if latest else None


@sync_to_async
def aget_credential_by_name(provider: str, name: str) -> Optional[Credential]:
    try:
        return Credential.objects.get(provider=provider, name=name)
    except Credential.DoesNotExist:
        return None


def get_credential_by_name(provider: str, name: str) -> Optional[Credential]:
    """Sync version of get_credential_by_name."""
    try:
        return Credential.objects.get(provider=provider, name=name)
    except Credential.DoesNotExist:
        return None


@sync_to_async
def alist_sprints(status: Optional[str] = None) -> list[Sprint]:
    qs = Sprint.objects.all()
    if status:
        qs = qs.filter(status=status)
    return list(qs)


@sync_to_async
def ainsert_sprint(
    sprint_id: str,
    name: str,
    status: str,
    start_date: str,
    end_date: str,
) -> Sprint:
    now = utc_now()
    return Sprint.objects.create(
        id=sprint_id,
        name=name,
        status=status,
        start_date=start_date,
        end_date=end_date,
        created_at=now,
        updated_at=now,
    )


@sync_to_async
def amove_open_tickets_to_sprint(old_sprint_id: str, new_sprint_id: str) -> int:
    now = utc_now()
    return Ticket.objects.filter(sprint_id=old_sprint_id, status__in=["open", "in-progress"]).update(sprint_id=new_sprint_id, updated_at=now)


@sync_to_async
def aupdate_sprint(sprint_id: str, **kwargs) -> bool:
    now = utc_now()
    kwargs["updated_at"] = now
    return Sprint.objects.filter(id=sprint_id).update(**kwargs) > 0


@sync_to_async
def alist_metric_definitions() -> list[MetricDefinition]:
    return list(MetricDefinition.objects.filter(enabled=True))


@sync_to_async
def alist_sessions(status: Optional[str] = None, project_id: Optional[str] = None) -> list[AgentSession]:
    return list_sessions(status=status, project_id=project_id)


@sync_to_async
def aget_session(session_id: str) -> Optional[AgentSession]:
    return get_session(session_id)


@sync_to_async
def aget_agent(agent_id: str) -> Optional[Agent]:
    return get_agent(agent_id)


@sync_to_async
def aget_vm_target(vm_id: str) -> Optional[VMTarget]:
    return get_vm_target(vm_id)


@sync_to_async
def ainsert_agent_metrics_log(
    log_id: str,
    agent_id: str,
    metric_definition_id: str,
    value: float,
) -> AgentMetricsLog:
    now = utc_now()
    return AgentMetricsLog.objects.create(
        id=log_id,
        agent_id=agent_id,
        metric_definition_id=metric_definition_id,
        value=value,
        recorded_at=now,
    )


# ============ Compatibility Classes ============


class AsyncDatabase:
    """Async database interface using Django ORM.

    This is a stateless wrapper that provides async methods.
    No global state, no settings manipulation.
    """

    # Marker attribute to distinguish from sync Database
    _sync_db = None

    def __init__(self, db_path: str = None):
        """Initialize. db_path is ignored (uses Django settings)."""
        pass

    async def initialize(self):
        """No-op for compatibility."""
        pass

    # Task sources
    async def list_task_sources(self):
        return await alist_task_sources()

    async def get_task_source(self, source_id: str):
        return await aget_task_source(source_id)

    async def upsert_task_source(self, source_id, enabled, base_priority, poll_interval_seconds, config):
        return await aupsert_task_source(source_id, enabled, base_priority, poll_interval_seconds, config)

    # Work items
    async def get_work_item(self, work_id: str):
        return await aget_work_item(work_id)

    async def update_work_item_status(self, work_id, status, **kwargs):
        return await aupdate_work_item_status(work_id, status, **kwargs)

    async def fetch_ready_work_items(self, now_iso: str):
        return await afetch_ready_work_items(now_iso)

    async def insert_work_item_if_absent(self, work_id, source_id, priority, checkpoint):
        return await ainsert_work_item_if_absent(work_id, source_id, priority, checkpoint)

    async def record_run_start(self, work_id: str):
        return await arecord_run_start(work_id)

    async def record_run_end(self, run_id: int, status: str, error: str = None):
        return await arecord_run_end(run_id, status, error)

    # Supervisor state
    async def update_supervisor_state(self, status, current_work_id=None, last_action=None, queue_depth=0):
        return await aupdate_supervisor_state(status, current_work_id, last_action, queue_depth)

    # Repo resources
    async def list_repo_resources_for_cleanup(self, cutoff_iso: str):
        return await alist_repo_resources_for_cleanup(cutoff_iso)

    async def delete_repo_resource(self, resource_id: str):
        return await adelete_repo_resource(resource_id)

    # Credentials
    async def get_latest_credential_update(self):
        return await aget_latest_credential_update()

    async def get_latest_github_token_update(self):
        return await aget_latest_github_token_update()

    async def get_latest_gitlab_token_update(self):
        return await aget_latest_gitlab_token_update()

    async def get_credential_by_name(self, provider: str, name: str):
        return await aget_credential_by_name(provider, name)

    # Sprints
    async def list_sprints(self, status: str = None):
        return await alist_sprints(status)

    async def insert_sprint(self, sprint_id, name, status, start_date, end_date):
        return await ainsert_sprint(sprint_id, name, status, start_date, end_date)

    async def move_open_tickets_to_sprint(self, old_sprint_id, new_sprint_id):
        return await amove_open_tickets_to_sprint(old_sprint_id, new_sprint_id)

    async def update_sprint(self, sprint_id, **kwargs):
        return await aupdate_sprint(sprint_id, **kwargs)

    # Metrics
    async def list_metric_definitions(self):
        return await alist_metric_definitions()

    async def insert_agent_metrics_log(self, log_id, agent_id, metric_definition_id, value):
        return await ainsert_agent_metrics_log(log_id, agent_id, metric_definition_id, value)

    # Sessions
    async def list_sessions(self, status: str = None, project_id: str = None):
        return await alist_sessions(status, project_id)

    async def get_session(self, session_id: str):
        return await aget_session(session_id)

    @sync_to_async
    def update_session(self, session_id: str, **kwargs):
        return update_session(session_id, **kwargs)

    @sync_to_async
    def insert_session(self, session_id, project_id, agent_id, ticket_id, status, repo_path, thread_ts=None, **kwargs):
        return insert_session(session_id, project_id, agent_id, ticket_id, status, repo_path, thread_ts, **kwargs)

    # Agents
    async def get_agent(self, agent_id: str):
        return await aget_agent(agent_id)

    @sync_to_async
    def get_agent_by_slug(self, slug: str):
        return get_agent_by_slug(slug)

    @sync_to_async
    def list_agents(self):
        return list_agents()

    # VM Targets
    async def get_vm_target(self, vm_id: str):
        return await aget_vm_target(vm_id)

    # Projects
    @sync_to_async
    def get_project(self, project_id: str):
        return get_project(project_id)

    @sync_to_async
    def get_project_by_slug(self, slug: str):
        return get_project_by_slug(slug)

    @sync_to_async
    def list_projects(self):
        return list_projects()

    # Tickets
    @sync_to_async
    def get_ticket(self, ticket_id: str):
        return get_ticket(ticket_id)

    @sync_to_async
    def update_ticket(self, ticket_id: str, **kwargs):
        return update_ticket(ticket_id, **kwargs)

    @sync_to_async
    def list_auto_start_tickets(self):
        return list_auto_start_tickets()

    # Comments
    @sync_to_async
    def insert_comment(self, comment_id, body, public, approved, sent=0, **kwargs):
        return insert_comment(comment_id, body, public, approved, sent, **kwargs)

    @sync_to_async
    def list_comments(self, approved=None, sent=None, public=None):
        return list_comments(approved, sent, public)

    @sync_to_async
    def update_comment(self, comment_id, **kwargs):
        return update_comment(comment_id, **kwargs)

    # Channels
    @sync_to_async
    def list_channels(self, agent_id: str = None):
        return list_channels(agent_id)

    @sync_to_async
    def get_channel_by_external_id(self, channel_type: str, external_id: str):
        return get_channel_by_external_id(channel_type, external_id)

    # Repo resources
    @sync_to_async
    def acquire_repo_resource(self, project, session_id, agent_id=None):
        return acquire_repo_resource(project, session_id, agent_id)

    @sync_to_async
    def release_repo_resource_for_session(self, session_id: str):
        return release_repo_resource_for_session(session_id)

    # Remote tokens
    @sync_to_async
    def list_remote_tokens(self, provider: str = None):
        return list_remote_tokens(provider)

    @sync_to_async
    def get_remote_token(self, token_id: str):
        return get_remote_token(token_id)

    # Insert methods for tests
    @sync_to_async
    def insert_project(self, project_id, name, slug, slack_channel_id, **kwargs):
        return insert_project(project_id, name, slug, slack_channel_id, **kwargs)

    @sync_to_async
    def insert_agent(self, agent_id, name, slug, command, session_mode, vm_target_id, **kwargs):
        return insert_agent(agent_id, name, slug, command, session_mode, vm_target_id, **kwargs)

    @sync_to_async
    def insert_channel(self, channel_id, agent_id, channel_type, name, external_channel_id=None, enabled=True):
        return insert_channel(channel_id, agent_id, channel_type, name, external_channel_id, enabled)

    @sync_to_async
    def list_github_sources(self):
        return list_github_sources()

    @sync_to_async
    def list_gitlab_sources(self):
        return list_gitlab_sources()

    @sync_to_async
    def get_github_token(self, token_id):
        token = get_remote_token(token_id)
        if token and token.provider == "github":
            return token
        return None

    @sync_to_async
    def get_gitlab_token(self, token_id):
        token = get_remote_token(token_id)
        if token and token.provider == "gitlab":
            return token
        return None


class Database:
    """Sync database interface using Django ORM.
    
    This is a stateless wrapper that provides sync methods.
    No global state, no settings manipulation.
    """

    def __init__(self, db_path: str = None):
        """Initialize. db_path is ignored (uses Django settings)."""
        pass

    def initialize(self):
        """No-op for compatibility."""
        pass

    # Provide sync versions of all methods
    def list_task_sources(self):
        return list_task_sources()

    def get_task_source(self, source_id: str):
        return get_task_source(source_id)

    def get_work_item(self, work_id: str):
        return get_work_item(work_id)

    def get_agent(self, agent_id: str):
        return get_agent(agent_id)

    def get_agent_by_slug(self, slug: str):
        return get_agent_by_slug(slug)

    def list_agents(self):
        return list_agents()

    def get_session(self, session_id: str):
        return get_session(session_id)

    def list_sessions(self, status: str = None, project_id: str = None):
        return list_sessions(status, project_id)

    def get_vm_target(self, vm_id: str):
        return get_vm_target(vm_id)

    def list_channels(self, agent_id: str = None):
        return list_channels(agent_id)

    def get_channel_by_external_id(self, channel_type: str, external_id: str):
        return get_channel_by_external_id(channel_type, external_id)

    def get_project(self, project_id: str):
        return get_project(project_id)

    def get_project_by_slug(self, slug: str):
        return get_project_by_slug(slug)

    def list_projects(self):
        return list_projects()

    def get_ticket(self, ticket_id: str):
        return get_ticket(ticket_id)

    def update_ticket(self, ticket_id: str, **kwargs):
        return update_ticket(ticket_id, **kwargs)

    def list_auto_start_tickets(self):
        return list_auto_start_tickets()

    def list_remote_tokens(self, provider: str = None):
        return list_remote_tokens(provider)

    def get_remote_token(self, token_id: str):
        return get_remote_token(token_id)

    def insert_session(self, session_id, project_id, agent_id, ticket_id, status, repo_path, thread_ts=None, **kwargs):
        return insert_session(session_id, project_id, agent_id, ticket_id, status, repo_path, thread_ts, **kwargs)

    def update_session(self, session_id: str, **kwargs):
        return update_session(session_id, **kwargs)

    def insert_comment(self, comment_id, body, public, approved, sent=0, **kwargs):
        return insert_comment(comment_id, body, public, approved, sent, **kwargs)

    def list_comments(self, approved=None, sent=None, public=None):
        return list_comments(approved, sent, public)

    def update_comment(self, comment_id, **kwargs):
        return update_comment(comment_id, **kwargs)

    def acquire_repo_resource(self, project, session_id, agent_id=None):
        return acquire_repo_resource(project, session_id, agent_id)

    def release_repo_resource_for_session(self, session_id: str):
        return release_repo_resource_for_session(session_id)

    # Insert methods
    def insert_project(self, project_id, name, slug, slack_channel_id, **kwargs):
        return insert_project(project_id, name, slug, slack_channel_id, **kwargs)

    def insert_agent(self, agent_id, name, slug, command, session_mode, vm_target_id, **kwargs):
        return insert_agent(agent_id, name, slug, command, session_mode, vm_target_id, **kwargs)

    def insert_vm_target(self, vm_id, name, host, user, port, **kwargs):
        return insert_vm_target(vm_id, name, host, user, port, **kwargs)

    def update_vm_target(self, vm_id, **kwargs):
        return update_vm_target(vm_id, **kwargs)

    def insert_channel(self, channel_id, agent_id, channel_type, name, external_channel_id=None, enabled=True):
        return insert_channel(channel_id, agent_id, channel_type, name, external_channel_id, enabled)

    def get_channel(self, channel_id):
        return get_channel(channel_id)

    def update_channel(self, channel_id, **kwargs):
        return update_channel(channel_id, **kwargs)

    def delete_channel(self, channel_id):
        return delete_channel(channel_id)

    # Metric definitions
    def insert_metric_definition(self, definition_id, metric_type, recording_frequency_minutes, enabled):
        return insert_metric_definition(definition_id, metric_type, recording_frequency_minutes, enabled)

    def get_metric_definition(self, definition_id):
        return get_metric_definition(definition_id)

    def list_metric_definitions(self):
        return list_metric_definitions()

    def update_metric_definition(self, definition_id, **kwargs):
        return update_metric_definition(definition_id, **kwargs)

    def delete_metric_definition(self, definition_id):
        return delete_metric_definition(definition_id)

    # Agent metrics
    def insert_agent_metrics_log(self, log_id, agent_id, metric_definition_id, value):
        return insert_agent_metrics_log_sync(log_id, agent_id, metric_definition_id, value)

    def get_agent_metrics_log(self, log_id):
        return get_agent_metrics_log(log_id)

    def list_agent_metrics_logs(self, agent_id=None, limit=None):
        return list_agent_metrics_logs(agent_id, limit)

    def get_agent_average_memory_usage(self, agent_id):
        return get_agent_average_memory_usage(agent_id)

    def refresh_agent_average_memory_usage(self, agent_id):
        return refresh_agent_average_memory_usage(agent_id)

    # Remote tokens
    def insert_remote_token(self, token_id, provider, token, note=None, user_id=None, user_login=None):
        return insert_remote_token(token_id, provider, token, note, user_id, user_login)

    def update_remote_token(self, token_id, **kwargs):
        return update_remote_token(token_id, **kwargs)

    def delete_remote_token(self, token_id):
        return delete_remote_token(token_id)

    # GitHub legacy methods
    def insert_github_token(self, token_id, token, note=None, user_id=None, user_login=None):
        return insert_remote_token(token_id, "github", token, note, user_id, user_login)

    def get_github_token(self, token_id):
        token = get_remote_token(token_id)
        if token and token.provider == "github":
            return token
        return None

    def list_github_tokens(self):
        return list_remote_tokens("github")

    def update_github_token(self, token_id, **kwargs):
        return update_remote_token(token_id, **kwargs)

    def delete_github_token(self, token_id):
        return delete_remote_token(token_id)

    def list_github_sources(self):
        return list_github_sources()

    def get_project_vm_for_project(self, project_id):
        return get_project_vm_for_project(project_id)

    # GitLab legacy methods
    def insert_gitlab_token(self, token_id, token, note=None, user_id=None, user_login=None):
        return insert_remote_token(token_id, "gitlab", token, note, user_id, user_login)

    def get_gitlab_token(self, token_id):
        token = get_remote_token(token_id)
        if token and token.provider == "gitlab":
            return token
        return None

    def list_gitlab_tokens(self):
        return list_remote_tokens("gitlab")

    def update_gitlab_token(self, token_id, **kwargs):
        return update_remote_token(token_id, **kwargs)

    def delete_gitlab_token(self, token_id):
        return delete_remote_token(token_id)

    # Session file configs
    def insert_session_file_config(self, config_id, name, description=None):
        return insert_session_file_config(config_id, name, description)

    def get_session_file_config(self, config_id):
        return get_session_file_config(config_id)

    def list_session_file_configs(self):
        return list_session_file_configs()

    def update_session_file_config(self, config_id, **kwargs):
        return update_session_file_config(config_id, **kwargs)

    def delete_session_file_config(self, config_id):
        return delete_session_file_config(config_id)

    # Session file definitions
    def insert_session_file_definition(self, definition_id, config_id, filename, default_content, **kwargs):
        return insert_session_file_definition(definition_id, config_id, filename, default_content, **kwargs)

    def get_session_file_definition(self, definition_id):
        return get_session_file_definition(definition_id)

    def list_session_file_definitions(self, config_id):
        return list_session_file_definitions(config_id)

    def update_session_file_definition(self, definition_id, **kwargs):
        return update_session_file_definition(definition_id, **kwargs)

    def delete_session_file_definition(self, definition_id):
        return delete_session_file_definition(definition_id)

    # Session files
    def insert_session_file(self, file_id, agent_id, definition_id, content):
        return insert_session_file(file_id, agent_id, definition_id, content)

    def get_session_file(self, file_id):
        return get_session_file(file_id)

    def get_session_file_by_definition(self, agent_id, definition_id):
        return get_session_file_by_definition(agent_id, definition_id)

    def list_session_files(self, agent_id):
        return list_session_files(agent_id)

    def update_session_file(self, file_id, **kwargs):
        return update_session_file(file_id, **kwargs)

    def delete_session_file(self, file_id):
        return delete_session_file(file_id)

    def delete_session_files_for_agent(self, agent_id):
        return delete_session_files_for_agent(agent_id)

    # Credentials
    def get_credential_by_name(self, provider: str, name: str):
        return get_credential_by_name(provider, name)
