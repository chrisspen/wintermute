"""SQLite persistence for Wintermute using SQLAlchemy ORM."""

from __future__ import annotations

import json
import re
import uuid
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from sqlalchemy import Integer, String, Text, UniqueConstraint, create_engine, func, inspect, select, or_
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

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


@dataclass(frozen=True)
class TaskSourceRecord:
    id: str
    enabled: bool
    base_priority: int
    poll_interval_seconds: int
    config: dict[str, Any]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class WorkItemRecord:
    work_id: str
    source_id: str
    priority: int
    status: str
    checkpoint: dict[str, Any]
    created_at: str
    updated_at: str
    run_after: str
    attempts: int
    last_error: Optional[str]
    last_traceback: Optional[str]


@dataclass(frozen=True)
class CredentialRecord:
    id: str
    name: str
    provider: str
    reference: str
    note: Optional[str]
    created_at: str


@dataclass(frozen=True)
class UserRecord:
    id: str
    username: str
    password_hash: str
    salt: str
    created_at: str


@dataclass(frozen=True)
class ColumnPreferenceRecord:
    id: str
    user_id: str
    model: str
    columns: list[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SupervisorStateRecord:
    id: str
    status: str
    current_work_id: Optional[str]
    last_action: str
    queue_depth: int
    updated_at: str


@dataclass(frozen=True)
class ApiTokenRecord:
    id: str
    name: str
    token: str
    permissions: dict[str, dict[str, bool]]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    name: str
    slug: str
    slack_channel_id: Optional[str]
    prompt_template: Optional[str]
    max_repo_resources: int
    repo_mode: Optional[str]
    repo_path: Optional[str]
    repo_url: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class IssueSourceRecord:
    id: str
    provider: str
    token_id: Optional[str]
    agent_id: Optional[str]
    project_id: str
    repo: str
    state: str
    labels: list[str]
    enabled: bool
    auto_start: bool
    poll_interval_seconds: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitHubSourceRecord:
    id: str
    token_id: Optional[str]
    agent_id: Optional[str]
    project_id: str
    owner: str
    repo: str
    state: str
    labels: list[str]
    enabled: bool
    auto_start: bool
    poll_interval_seconds: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitLabSourceRecord:
    id: str
    token_id: Optional[str]
    agent_id: Optional[str]
    project_id: str
    project_path: str
    state: str
    labels: list[str]
    enabled: bool
    auto_start: bool
    poll_interval_seconds: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitHubTokenRecord:
    id: str
    note: Optional[str]
    token: str
    user_id: Optional[str]
    user_login: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GitLabTokenRecord:
    id: str
    note: Optional[str]
    token: str
    base_url: Optional[str]
    user_id: Optional[str]
    user_login: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RemoteTokenRecord:
    id: str
    provider: str
    note: Optional[str]
    token: str
    base_url: Optional[str]
    user_id: Optional[str]
    user_login: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class SprintRecord:
    id: str
    name: str
    start_date: str
    end_date: str
    enabled: bool
    status: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TicketRecord:
    id: str
    project_id: str
    agent_id: Optional[str]
    sprint_id: Optional[str]
    title: str
    description: Optional[str]
    internal_notes: Optional[str]
    assigned_to: Optional[str]
    estimate: Optional[str]
    hours: Optional[float]
    story_points: Optional[float]
    priority: Optional[str]
    status: str
    source_url: Optional[str]
    github_comments_json: Optional[str]
    github_comments_fetched_at: Optional[str]
    auto_start: bool
    created_by_id: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CommentRecord:
    id: str
    ticket_id: str
    session_id: Optional[str]
    project_id: Optional[str]
    agent_id: Optional[str]
    author: Optional[str]
    source_id: Optional[str]
    issue_number: Optional[int]
    body: str
    public: bool
    approved: bool
    sent: bool
    sent_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RepoResourceRecord:
    id: str
    project_id: str
    agent_id: Optional[str]
    repo_mode: str
    path: str
    status: str
    session_id: Optional[str]
    last_used_at: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class VMTargetRecord:
    id: str
    name: str
    host: str
    user: str
    port: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentRecord:
    id: str
    name: str
    slug: str
    command: str
    session_mode: str
    vm_target_id: Optional[str]
    required_ssh_options: Optional[str]
    env_vars: Optional[str]
    mcp_config: Optional[str]
    trust_level: Optional[str]
    input_echo_prefix: Optional[str]
    response_prefix: Optional[str]
    llm_base_url: Optional[str]
    llm_api_key: Optional[str]
    llm_model: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentSessionRecord:
    id: str
    project_id: str
    agent_id: str
    ticket_id: Optional[str]
    status: str
    repo_path: str
    thread_ts: Optional[str]
    mcp_conversation_id: Optional[str]
    claude_session_id: Optional[str]
    last_output: Optional[str]
    last_output_offset: int
    output_buffer: Optional[str]
    output_buffer_updated_at: Optional[str]
    prompt_pending: Optional[str]
    prompt_sent_at: Optional[str]
    last_output_at: Optional[str]
    awaiting_response: int
    last_user_message: Optional[str]
    queued_user_messages: Optional[str]
    awaiting_response_offset: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentResponseRecord:
    id: str
    agent_id: str
    pattern: str
    response: str
    created_at: str
    updated_at: str


class Base(DeclarativeBase):
    pass


class TaskSourceModel(Base):
    __tablename__ = "task_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False)
    base_priority: Mapped[int] = mapped_column(Integer, nullable=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class WorkItemModel(Base):
    __tablename__ = "work_items"

    work_id: Mapped[str] = mapped_column(String, primary_key=True)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    checkpoint_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
    run_after: Mapped[str] = mapped_column(String, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_traceback: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class WorkItemRunModel(Base):
    __tablename__ = "work_item_runs"

    run_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    work_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    ended_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class CredentialModel(Base):
    __tablename__ = "credentials"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    reference: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    salt: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class ColumnPreferenceModel(Base):
    __tablename__ = "ui_column_preferences"
    __table_args__ = (UniqueConstraint("user_id", "model", name="uq_ui_column_preferences_user_model"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    columns_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class ApiTokenModel(Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    token: Mapped[str] = mapped_column(String, nullable=False)
    permissions_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class SupervisorStateModel(Base):
    __tablename__ = "supervisor_state"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    current_work_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_action: Mapped[str] = mapped_column(String, nullable=False)
    queue_depth: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class ProjectModel(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    slack_channel_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_template: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    max_repo_resources: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    repo_mode: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    repo_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    repo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class SprintModel(Base):
    __tablename__ = "sprints"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    start_date: Mapped[str] = mapped_column(String, nullable=False)
    end_date: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class TicketSprintModel(Base):
    __tablename__ = "ticket_sprints"

    ticket_id: Mapped[str] = mapped_column(String, primary_key=True)
    sprint_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class TicketModel(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    sprint_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    estimate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    hours: Mapped[Optional[float]] = mapped_column(String, nullable=True)
    story_points: Mapped[Optional[float]] = mapped_column(String, nullable=True)
    priority: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_comments_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    github_comments_fetched_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    auto_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class CommentModel(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    author: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    issue_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    public: Mapped[int] = mapped_column(Integer, nullable=False)
    approved: Mapped[int] = mapped_column(Integer, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class RepoResourceModel(Base):
    __tablename__ = "repo_resources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    repo_mode: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_used_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class VMTargetModel(Base):
    __tablename__ = "vm_targets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    host: Mapped[str] = mapped_column(String, nullable=False)
    user: Mapped[str] = mapped_column(String, nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class IssueSourceModel(Base):
    __tablename__ = "issue_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    token_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_start: Mapped[int] = mapped_column(Integer, nullable=False)
    poll_interval_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class GitHubTokenModel(Base):
    __tablename__ = "github_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_login: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class GitLabTokenModel(Base):
    __tablename__ = "gitlab_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_login: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class RemoteTokenModel(Base):
    __tablename__ = "remote_tokens"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_login: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    command: Mapped[str] = mapped_column(String, nullable=False)
    session_mode: Mapped[str] = mapped_column(String, nullable=False, default="tmux")
    vm_target_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    required_ssh_options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    env_vars: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mcp_config: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    trust_level: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    input_echo_prefix: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    response_prefix: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    llm_base_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    llm_api_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AgentSessionModel(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    ticket_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    repo_path: Mapped[str] = mapped_column(String, nullable=False)
    thread_ts: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    mcp_conversation_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    claude_session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_output_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    output_buffer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_buffer_updated_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    prompt_pending: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    prompt_sent_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_output_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    awaiting_response: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_user_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    queued_user_messages: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    awaiting_response_offset: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AgentResponseModel(Base):
    __tablename__ = "agent_responses"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    pattern: Mapped[str] = mapped_column(Text, nullable=False)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class Database:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or os.environ.get(DEFAULT_DB_PATH_ENV, "./wintermute.db")
        self.engine: Engine = create_engine(f"sqlite:///{self.path}", future=True)
        self._session_factory = sessionmaker(bind=self.engine, future=True, expire_on_commit=False)

    @contextmanager
    def session(self) -> Generator[Session, None, None]:
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def initialize(self) -> None:
        tables = inspect(self.engine).get_table_names()
        if not tables and os.environ.get("WINTERMUTE_AUTO_MIGRATE", "1") == "1":
            Base.metadata.create_all(self.engine)

    def upsert_task_source(
        self,
        source_id: str,
        enabled: bool,
        base_priority: int,
        poll_interval_seconds: int,
        config: dict[str, Any],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            model = session.get(TaskSourceModel, source_id)
            if model is None:
                model = TaskSourceModel(
                    id=source_id,
                    enabled=1 if enabled else 0,
                    base_priority=base_priority,
                    poll_interval_seconds=poll_interval_seconds,
                    config_json=json_dumps(config),
                    created_at=now,
                    updated_at=now,
                )
                session.add(model)
            else:
                model.enabled = 1 if enabled else 0
                model.base_priority = base_priority
                model.poll_interval_seconds = poll_interval_seconds
                model.config_json = json_dumps(config)
                model.updated_at = now

    def list_task_sources(self) -> list[TaskSourceRecord]:
        with self.session() as session:
            rows = session.execute(select(TaskSourceModel).order_by(TaskSourceModel.id)).scalars().all()
        return [
            TaskSourceRecord(
                id=row.id,
                enabled=bool(row.enabled),
                base_priority=row.base_priority,
                poll_interval_seconds=row.poll_interval_seconds,
                config=json_loads(row.config_json),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_task_source(self, source_id: str) -> Optional[TaskSourceRecord]:
        with self.session() as session:
            row = session.get(TaskSourceModel, source_id)
        if not row:
            return None
        return TaskSourceRecord(
            id=row.id,
            enabled=bool(row.enabled),
            base_priority=row.base_priority,
            poll_interval_seconds=row.poll_interval_seconds,
            config=json_loads(row.config_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def delete_task_source(self, source_id: str) -> None:
        with self.session() as session:
            session.query(TaskSourceModel).filter(TaskSourceModel.id == source_id).delete()

    def insert_work_item_if_absent(
        self,
        work_id: str,
        source_id: str,
        priority: int,
        checkpoint: dict[str, Any],
        status: str = "queued",
    ) -> bool:
        now = utc_now()
        model = WorkItemModel(
            work_id=work_id,
            source_id=source_id,
            priority=priority,
            status=status,
            checkpoint_json=json_dumps(checkpoint),
            created_at=now,
            updated_at=now,
            run_after=now,
            attempts=0,
            last_error=None,
            last_traceback=None,
        )
        try:
            with self.session() as session:
                session.add(model)
            return True
        except IntegrityError:
            return False

    def fetch_ready_work_items(self, now: str) -> list[WorkItemRecord]:
        with self.session() as session:
            rows = (
                session.execute(
                    select(WorkItemModel)
                    .where(WorkItemModel.status == "queued", WorkItemModel.run_after <= now)
                    .order_by(WorkItemModel.priority.asc(), WorkItemModel.created_at.asc())
                )
                .scalars()
                .all()
            )
        return [self._model_to_work_item(row) for row in rows]

    def list_work_items(self, status: Optional[str] = None) -> list[WorkItemRecord]:
        with self.session() as session:
            stmt = select(WorkItemModel)
            if status:
                stmt = stmt.where(WorkItemModel.status == status)
            rows = (
                session.execute(stmt.order_by(WorkItemModel.priority.asc(), WorkItemModel.created_at.asc()))
                .scalars()
                .all()
            )
        return [self._model_to_work_item(row) for row in rows]

    def get_work_item(self, work_id: str) -> Optional[WorkItemRecord]:
        with self.session() as session:
            row = session.get(WorkItemModel, work_id)
        return self._model_to_work_item(row) if row else None

    def delete_work_item(self, work_id: str) -> None:
        with self.session() as session:
            session.query(WorkItemModel).filter(WorkItemModel.work_id == work_id).delete()

    def update_work_item_status(
        self,
        work_id: str,
        status: str,
        *,
        checkpoint: Optional[dict[str, Any]] = None,
        priority: Optional[int] = None,
        run_after: Optional[str] = None,
        attempts: Optional[int] = None,
        last_error: Optional[str] = None,
        last_traceback: Optional[str] = None,
        clear_errors: bool = False,
    ) -> None:
        with self.session() as session:
            model = session.get(WorkItemModel, work_id)
            if model is None:
                return
            model.status = status
            model.updated_at = utc_now()
            if checkpoint is not None:
                model.checkpoint_json = json_dumps(checkpoint)
            if priority is not None:
                model.priority = priority
            if run_after is not None:
                model.run_after = run_after
            if attempts is not None:
                model.attempts = attempts
            if clear_errors:
                model.last_error = None
                model.last_traceback = None
            else:
                if last_error is not None:
                    model.last_error = last_error
                if last_traceback is not None:
                    model.last_traceback = last_traceback

    def record_run_start(self, work_id: str) -> int:
        with self.session() as session:
            run = WorkItemRunModel(
                work_id=work_id,
                started_at=utc_now(),
                ended_at=None,
                status="running",
                error=None,
            )
            session.add(run)
            session.flush()
            return int(run.run_id)

    def record_run_end(
        self,
        run_id: int,
        status: str,
        error: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            run = session.get(WorkItemRunModel, run_id)
            if run is None:
                return
            run.ended_at = utc_now()
            run.status = status
            run.error = error

    def update_supervisor_state(
        self,
        status: str,
        current_work_id: Optional[str],
        last_action: str,
        queue_depth: int,
    ) -> None:
        with self.session() as session:
            state = session.get(SupervisorStateModel, "primary")
            if state is None:
                state = SupervisorStateModel(
                    id="primary",
                    status=status,
                    current_work_id=current_work_id,
                    last_action=last_action,
                    queue_depth=queue_depth,
                    updated_at=utc_now(),
                )
                session.add(state)
                return
            state.status = status
            state.current_work_id = current_work_id
            state.last_action = last_action
            state.queue_depth = queue_depth
            state.updated_at = utc_now()

    def get_supervisor_state(self) -> Optional[SupervisorStateRecord]:
        with self.session() as session:
            state = session.get(SupervisorStateModel, "primary")
        if not state:
            return None
        return SupervisorStateRecord(
            id=state.id,
            status=state.status,
            current_work_id=state.current_work_id,
            last_action=state.last_action,
            queue_depth=state.queue_depth,
            updated_at=state.updated_at,
        )

    def list_credentials(self) -> list[CredentialRecord]:
        with self.session() as session:
            rows = session.execute(select(CredentialModel).order_by(CredentialModel.name)).scalars().all()
        return [
            CredentialRecord(
                id=row.id,
                name=row.name,
                provider=row.provider,
                reference=row.reference,
                note=row.note,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_latest_credential_update(self) -> str:
        with self.session() as session:
            rows = session.execute(
                select(
                    CredentialModel.id,
                    CredentialModel.name,
                    CredentialModel.provider,
                    CredentialModel.reference,
                    CredentialModel.note,
                    CredentialModel.created_at,
                ).order_by(CredentialModel.id)
            ).all()
        return json_dumps([tuple(row) for row in rows])

    def insert_credential(
        self,
        cred_id: str,
        name: str,
        provider: str,
        reference: str,
        note: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            session.add(
                CredentialModel(
                    id=cred_id,
                    name=name,
                    provider=provider,
                    reference=reference,
                    note=note,
                    created_at=utc_now(),
                )
            )

    def upsert_credential(
        self,
        cred_id: str,
        name: str,
        provider: str,
        reference: str,
        note: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            existing = session.get(CredentialModel, cred_id)
            if existing is None:
                session.add(
                    CredentialModel(
                        id=cred_id,
                        name=name,
                        provider=provider,
                        reference=reference,
                        note=note,
                        created_at=utc_now(),
                    )
                )
                return
            existing.name = name
            existing.provider = provider
            existing.reference = reference
            if note is not None:
                existing.note = note

    def update_credential(
        self,
        cred_id: str,
        *,
        name: Optional[str] = None,
        provider: Optional[str] = None,
        reference: Optional[str] = None,
        note: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(CredentialModel, cred_id)
            if not row:
                return
            if name is not None:
                row.name = name
            if provider is not None:
                row.provider = provider
            if reference is not None:
                row.reference = reference
            if note is not None:
                row.note = note

    def delete_credential(self, cred_id: str) -> None:
        with self.session() as session:
            session.query(CredentialModel).filter(CredentialModel.id == cred_id).delete()

    def get_credential(self, cred_id: str) -> Optional[CredentialRecord]:
        with self.session() as session:
            row = session.get(CredentialModel, cred_id)
        if not row:
            return None
        return CredentialRecord(
            id=row.id,
            name=row.name,
            provider=row.provider,
            reference=row.reference,
            note=row.note,
            created_at=row.created_at,
        )

    def get_credential_by_name(self, provider: str, name: str) -> Optional[CredentialRecord]:
        with self.session() as session:
            row = (
                session.execute(
                    select(CredentialModel)
                    .where(CredentialModel.provider == provider, CredentialModel.name == name)
                )
                .scalar_one_or_none()
            )
        if not row:
            return None
        return CredentialRecord(
            id=row.id,
            name=row.name,
            provider=row.provider,
            reference=row.reference,
            note=row.note,
            created_at=row.created_at,
        )

    def list_users(self) -> list[UserRecord]:
        with self.session() as session:
            rows = session.execute(select(UserModel).order_by(UserModel.username)).scalars().all()
        return [
            UserRecord(
                id=row.id,
                username=row.username,
                password_hash=row.password_hash,
                salt=row.salt,
                created_at=row.created_at,
            )
            for row in rows
        ]

    def get_user(self, username: str) -> Optional[UserRecord]:
        with self.session() as session:
            row = session.execute(select(UserModel).where(UserModel.username == username)).scalar_one_or_none()
        if not row:
            return None
        return UserRecord(
            id=row.id,
            username=row.username,
            password_hash=row.password_hash,
            salt=row.salt,
            created_at=row.created_at,
        )

    def get_user_by_id(self, user_id: str) -> Optional[UserRecord]:
        with self.session() as session:
            row = session.get(UserModel, user_id)
        if not row:
            return None
        return UserRecord(
            id=row.id,
            username=row.username,
            password_hash=row.password_hash,
            salt=row.salt,
            created_at=row.created_at,
        )

    def insert_user(self, user_id: str, username: str, password_hash: str, salt: str) -> None:
        with self.session() as session:
            session.add(
                UserModel(
                    id=user_id,
                    username=username,
                    password_hash=password_hash,
                    salt=salt,
                    created_at=utc_now(),
                )
            )

    def update_user(
        self,
        user_id: str,
        *,
        username: Optional[str] = None,
        password_hash: Optional[str] = None,
        salt: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(UserModel, user_id)
            if not row:
                return
            if username is not None:
                row.username = username
            if password_hash is not None:
                row.password_hash = password_hash
            if salt is not None:
                row.salt = salt

    def delete_user(self, user_id: str) -> None:
        with self.session() as session:
            session.query(UserModel).filter(UserModel.id == user_id).delete()

    def get_column_preferences(self, user_id: str, model: str) -> Optional[ColumnPreferenceRecord]:
        with self.session() as session:
            row = session.execute(
                select(ColumnPreferenceModel).where(
                    ColumnPreferenceModel.user_id == user_id,
                    ColumnPreferenceModel.model == model,
                )
            ).scalar_one_or_none()
        if not row:
            return None
        columns = json_loads(row.columns_json)
        if not isinstance(columns, list):
            columns = []
        return ColumnPreferenceRecord(
            id=row.id,
            user_id=row.user_id,
            model=row.model,
            columns=[str(item) for item in columns if item],
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def upsert_column_preferences(self, user_id: str, model: str, columns: list[str]) -> None:
        now = utc_now()
        payload = json_dumps(columns)
        with self.session() as session:
            row = session.execute(
                select(ColumnPreferenceModel).where(
                    ColumnPreferenceModel.user_id == user_id,
                    ColumnPreferenceModel.model == model,
                )
            ).scalar_one_or_none()
            if row:
                row.columns_json = payload
                row.updated_at = now
            else:
                session.add(
                    ColumnPreferenceModel(
                        id=str(uuid.uuid4()),
                        user_id=user_id,
                        model=model,
                        columns_json=payload,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def list_api_tokens(self) -> list[ApiTokenRecord]:
        with self.session() as session:
            rows = session.execute(select(ApiTokenModel).order_by(ApiTokenModel.created_at.desc())).scalars().all()
        return [
            ApiTokenRecord(
                id=row.id,
                name=row.name,
                token=row.token,
                permissions=json_loads(row.permissions_json) or {},
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_api_token(self, token_id: str) -> Optional[ApiTokenRecord]:
        with self.session() as session:
            row = session.get(ApiTokenModel, token_id)
        if not row:
            return None
        return ApiTokenRecord(
            id=row.id,
            name=row.name,
            token=row.token,
            permissions=json_loads(row.permissions_json) or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_api_token_by_value(self, token_value: str) -> Optional[ApiTokenRecord]:
        with self.session() as session:
            row = session.execute(
                select(ApiTokenModel).where(ApiTokenModel.token == token_value)
            ).scalar_one_or_none()
        if not row:
            return None
        return ApiTokenRecord(
            id=row.id,
            name=row.name,
            token=row.token,
            permissions=json_loads(row.permissions_json) or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_api_token(
        self,
        token_id: str,
        name: str,
        token: str,
        permissions: dict[str, dict[str, bool]],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                ApiTokenModel(
                    id=token_id,
                    name=name,
                    token=token,
                    permissions_json=json_dumps(permissions),
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_api_token(
        self,
        token_id: str,
        *,
        name: Optional[str] = None,
        token: Optional[str] = None,
        permissions: Optional[dict[str, dict[str, bool]]] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(ApiTokenModel, token_id)
            if not row:
                return
            if name is not None:
                row.name = name
            if token is not None:
                row.token = token
            if permissions is not None:
                row.permissions_json = json_dumps(permissions)
            row.updated_at = utc_now()

    def delete_api_token(self, token_id: str) -> None:
        with self.session() as session:
            session.query(ApiTokenModel).filter(ApiTokenModel.id == token_id).delete()

    def list_projects(self) -> list[ProjectRecord]:
        with self.session() as session:
            rows = session.execute(select(ProjectModel).order_by(ProjectModel.name)).scalars().all()
        return [
            ProjectRecord(
                id=row.id,
                name=row.name,
                slug=row.slug,
                slack_channel_id=row.slack_channel_id,
                prompt_template=row.prompt_template,
                max_repo_resources=row.max_repo_resources,
                repo_mode=row.repo_mode,
                repo_path=row.repo_path,
                repo_url=row.repo_url,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_project(self, project_id: str) -> Optional[ProjectRecord]:
        with self.session() as session:
            row = session.get(ProjectModel, project_id)
        if not row:
            return None
        return ProjectRecord(
            id=row.id,
            name=row.name,
            slug=row.slug,
            slack_channel_id=row.slack_channel_id,
            prompt_template=row.prompt_template,
            max_repo_resources=row.max_repo_resources,
            repo_mode=row.repo_mode,
            repo_path=row.repo_path,
            repo_url=row.repo_url,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_project_by_slug(self, slug: str) -> Optional[ProjectRecord]:
        with self.session() as session:
            row = session.execute(select(ProjectModel).where(ProjectModel.slug == slug)).scalar_one_or_none()
        if not row:
            return None
        return ProjectRecord(
            id=row.id,
            name=row.name,
            slug=row.slug,
            slack_channel_id=row.slack_channel_id,
            prompt_template=row.prompt_template,
            max_repo_resources=row.max_repo_resources,
            repo_mode=row.repo_mode,
            repo_path=row.repo_path,
            repo_url=row.repo_url,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_project(
        self,
        project_id: str,
        name: str,
        slug: str,
        slack_channel_id: Optional[str],
        prompt_template: Optional[str] = None,
        max_repo_resources: int = 3,
        repo_mode: Optional[str] = None,
        repo_path: Optional[str] = None,
        repo_url: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                ProjectModel(
                    id=project_id,
                    name=name,
                    slug=slug,
                    slack_channel_id=slack_channel_id,
                    prompt_template=prompt_template,
                    max_repo_resources=max_repo_resources,
                    repo_mode=repo_mode,
                    repo_path=repo_path,
                    repo_url=repo_url,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_project(
        self,
        project_id: str,
        *,
        name: Optional[str] = None,
        slug: Optional[str] = None,
        slack_channel_id: Optional[str] = None,
        prompt_template: Optional[str] = None,
        max_repo_resources: Optional[int] = None,
        repo_mode: Optional[str] = None,
        repo_path: Optional[str] = None,
        repo_url: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(ProjectModel, project_id)
            if not row:
                return
            if name is not None:
                row.name = name
            if slug is not None:
                row.slug = slug
            if slack_channel_id is not None:
                row.slack_channel_id = slack_channel_id
            if prompt_template is not None:
                row.prompt_template = prompt_template
            if max_repo_resources is not None:
                row.max_repo_resources = max_repo_resources
            if repo_mode is not None:
                row.repo_mode = repo_mode
            if repo_path is not None:
                row.repo_path = repo_path
            if repo_url is not None:
                row.repo_url = repo_url
            row.updated_at = utc_now()

    def delete_project(self, project_id: str) -> None:
        with self.session() as session:
            session.query(AgentSessionModel).filter(
                AgentSessionModel.project_id == project_id
            ).delete()
            session.query(RepoResourceModel).filter(
                RepoResourceModel.project_id == project_id
            ).delete()
            session.query(IssueSourceModel).filter(
                IssueSourceModel.project_id == project_id
            ).delete()
            session.query(TicketModel).filter(TicketModel.project_id == project_id).delete()
            session.query(ProjectModel).filter(ProjectModel.id == project_id).delete()

    def list_github_tokens(self) -> list[GitHubTokenRecord]:
        """Legacy wrapper - returns tokens from unified remote_tokens table."""
        tokens = self.list_remote_tokens(provider="github")
        return [
            GitHubTokenRecord(
                id=t.id,
                note=t.note,
                token=t.token,
                user_id=t.user_id,
                user_login=t.user_login,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in tokens
        ]

    def list_gitlab_tokens(self) -> list[GitLabTokenRecord]:
        """Legacy wrapper - returns tokens from unified remote_tokens table."""
        tokens = self.list_remote_tokens(provider="gitlab")
        return [
            GitLabTokenRecord(
                id=t.id,
                note=t.note,
                token=t.token,
                base_url=t.base_url,
                user_id=t.user_id,
                user_login=t.user_login,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in tokens
        ]

    def list_repo_resources(self) -> list[RepoResourceRecord]:
        with self.session() as session:
            rows = session.execute(
                select(RepoResourceModel).order_by(RepoResourceModel.updated_at.desc())
            ).scalars().all()
        return [
            RepoResourceRecord(
                id=row.id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                repo_mode=row.repo_mode,
                path=row.path,
                status=row.status,
                session_id=row.session_id,
                last_used_at=row.last_used_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_repo_resource(self, resource_id: str) -> Optional[RepoResourceRecord]:
        with self.session() as session:
            row = session.get(RepoResourceModel, resource_id)
        if not row:
            return None
        return RepoResourceRecord(
            id=row.id,
            project_id=row.project_id,
            agent_id=row.agent_id,
            repo_mode=row.repo_mode,
            path=row.path,
            status=row.status,
            session_id=row.session_id,
            last_used_at=row.last_used_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def acquire_repo_resource(
        self,
        *,
        project: ProjectRecord,
        session_id: str,
        agent_id: Optional[str],
    ) -> tuple[Optional[RepoResourceRecord], Optional[str]]:
        """Acquire a repo resource for a session. Repo config comes from Project."""
        now = utc_now()
        repo_mode = project.repo_mode or "mirror"
        repo_path = project.repo_path

        def _to_record(row: RepoResourceModel) -> RepoResourceRecord:
            return RepoResourceRecord(
                id=row.id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                repo_mode=row.repo_mode,
                path=row.path,
                status=row.status,
                session_id=row.session_id,
                last_used_at=row.last_used_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

        with self.session() as session:
            rows = session.execute(
                select(RepoResourceModel)
                .where(RepoResourceModel.project_id == project.id)
                .order_by(RepoResourceModel.last_used_at.asc().nullsfirst(), RepoResourceModel.created_at.asc())
            ).scalars().all()
            for row in rows:
                if row.status != "in_use" or not row.session_id:
                    continue
                session_row = session.get(AgentSessionModel, row.session_id)
                if not session_row or session_row.status != "running":
                    row.status = "available"
                    row.session_id = None
                    row.agent_id = None
                    row.last_used_at = now
                    row.updated_at = now
            if repo_mode == "mirror":
                if not repo_path:
                    return None, "mirror path not configured"
                existing = next((row for row in rows if row.path == repo_path), None)
                if existing:
                    if existing.status == "in_use":
                        return None, "mirror repo already in use"
                    existing.status = "in_use"
                    existing.session_id = session_id
                    existing.agent_id = agent_id
                    existing.last_used_at = now
                    existing.updated_at = now
                    session.flush()
                    return _to_record(existing), None
                new_resource = RepoResourceModel(
                    id=str(uuid.uuid4()),
                    project_id=project.id,
                    agent_id=agent_id,
                    repo_mode=repo_mode,
                    path=repo_path,
                    status="in_use",
                    session_id=session_id,
                    last_used_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_resource)
                session.flush()
                return _to_record(new_resource), None

            if repo_mode == "local":
                # Local mode: path is /home/<user>/git/<project-id> but we don't know user here
                # Use a placeholder path that will be resolved by runner.ensure_repo
                local_path = f"local:{project.id}"
                existing = next((row for row in rows if row.path == local_path), None)
                if existing:
                    if existing.status == "in_use":
                        return None, "local repo already in use"
                    existing.status = "in_use"
                    existing.session_id = session_id
                    existing.agent_id = agent_id
                    existing.last_used_at = now
                    existing.updated_at = now
                    session.flush()
                    return _to_record(existing), None
                new_resource = RepoResourceModel(
                    id=str(uuid.uuid4()),
                    project_id=project.id,
                    agent_id=agent_id,
                    repo_mode=repo_mode,
                    path=local_path,
                    status="in_use",
                    session_id=session_id,
                    last_used_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(new_resource)
                session.flush()
                return _to_record(new_resource), None

            available = [row for row in rows if row.status != "in_use"]
            if available:
                row = available[0]
                row.status = "in_use"
                row.session_id = session_id
                row.agent_id = agent_id
                row.last_used_at = now
                row.updated_at = now
                session.flush()
                return _to_record(row), None
            if len(rows) >= max(project.max_repo_resources, 1):
                return None, "repo resource limit reached"
            if not repo_path:
                return None, "repo path not configured"
            safe_suffix = re.sub(r"[^a-zA-Z0-9]+", "-", session_id.strip().lower()).strip("-")
            if not safe_suffix:
                safe_suffix = "session"
            path = f"{repo_path}-{safe_suffix}"
            new_resource = RepoResourceModel(
                id=str(uuid.uuid4()),
                project_id=project.id,
                agent_id=agent_id,
                repo_mode=repo_mode,
                path=path,
                status="in_use",
                session_id=session_id,
                last_used_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(new_resource)
            session.flush()
            return _to_record(new_resource), None

    def release_repo_resource_for_session(self, session_id: str) -> None:
        now = utc_now()
        with self.session() as session:
            row = session.execute(
                select(RepoResourceModel).where(RepoResourceModel.session_id == session_id)
            ).scalar_one_or_none()
            if not row:
                return
            row.status = "available"
            row.session_id = None
            row.agent_id = None
            row.last_used_at = now
            row.updated_at = now

    def list_repo_resources_for_cleanup(self, cutoff: str) -> list[RepoResourceRecord]:
        with self.session() as session:
            rows = session.execute(
                select(RepoResourceModel)
                .where(RepoResourceModel.status == "available")
                .where(RepoResourceModel.repo_mode == "clone")
                .where(
                    or_(
                        RepoResourceModel.last_used_at.is_(None),
                        RepoResourceModel.last_used_at <= cutoff,
                    )
                )
            ).scalars().all()
        return [
            RepoResourceRecord(
                id=row.id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                repo_mode=row.repo_mode,
                path=row.path,
                status=row.status,
                session_id=row.session_id,
                last_used_at=row.last_used_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def delete_repo_resource(self, resource_id: str) -> None:
        with self.session() as session:
            session.query(RepoResourceModel).filter(RepoResourceModel.id == resource_id).delete()

    def insert_repo_resource(
        self,
        resource_id: str,
        project_id: str,
        repo_mode: str,
        path: str,
        status: str,
        *,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                RepoResourceModel(
                    id=resource_id,
                    project_id=project_id,
                    agent_id=agent_id,
                    repo_mode=repo_mode,
                    path=path,
                    status=status,
                    session_id=session_id,
                    last_used_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_repo_resource(
        self,
        resource_id: str,
        *,
        status: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        last_used_at: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(RepoResourceModel, resource_id)
            if not row:
                return
            if status is not None:
                row.status = status
            if session_id is not None:
                row.session_id = session_id
            if agent_id is not None:
                row.agent_id = agent_id
            if last_used_at is not None:
                row.last_used_at = last_used_at
            row.updated_at = utc_now()

    def get_latest_github_token_update(self) -> str:
        """Legacy wrapper - uses unified remote_tokens table."""
        with self.session() as session:
            value = session.execute(
                select(func.max(RemoteTokenModel.updated_at)).where(RemoteTokenModel.provider == "github")
            ).scalar_one_or_none()
        return value or ""

    def get_latest_gitlab_token_update(self) -> str:
        """Legacy wrapper - uses unified remote_tokens table."""
        with self.session() as session:
            value = session.execute(
                select(func.max(RemoteTokenModel.updated_at)).where(RemoteTokenModel.provider == "gitlab")
            ).scalar_one_or_none()
        return value or ""

    def get_github_token(self, token_id: str) -> Optional[GitHubTokenRecord]:
        """Legacy wrapper - uses unified remote_tokens table."""
        t = self.get_remote_token(token_id)
        if not t or t.provider != "github":
            return None
        return GitHubTokenRecord(
            id=t.id,
            note=t.note,
            token=t.token,
            user_id=t.user_id,
            user_login=t.user_login,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )

    def insert_github_token(
        self,
        token_id: str,
        token: str,
        note: Optional[str],
        user_id: Optional[str],
        user_login: Optional[str],
    ) -> None:
        """Legacy wrapper - uses unified remote_tokens table."""
        self.insert_remote_token(
            token_id,
            provider="github",
            token=token,
            note=note,
            user_id=user_id,
            user_login=user_login,
        )

    def update_github_token(
        self,
        token_id: str,
        *,
        token: Optional[str] = None,
        note: Optional[str] = None,
        user_id: Optional[str] = None,
        user_login: Optional[str] = None,
    ) -> None:
        """Legacy wrapper - uses unified remote_tokens table."""
        self.update_remote_token(
            token_id,
            token=token,
            note=note,
            user_id=user_id,
            user_login=user_login,
        )

    def delete_github_token(self, token_id: str) -> None:
        """Legacy wrapper - uses unified remote_tokens table."""
        self.delete_remote_token(token_id)

    def get_gitlab_token(self, token_id: str) -> Optional[GitLabTokenRecord]:
        """Legacy wrapper - uses unified remote_tokens table."""
        t = self.get_remote_token(token_id)
        if not t or t.provider != "gitlab":
            return None
        return GitLabTokenRecord(
            id=t.id,
            note=t.note,
            token=t.token,
            base_url=t.base_url,
            user_id=t.user_id,
            user_login=t.user_login,
            created_at=t.created_at,
            updated_at=t.updated_at,
        )

    def insert_gitlab_token(
        self,
        token_id: str,
        token: str,
        note: Optional[str],
        user_id: Optional[str],
        user_login: Optional[str],
    ) -> None:
        """Legacy wrapper - uses unified remote_tokens table."""
        self.insert_remote_token(
            token_id,
            provider="gitlab",
            token=token,
            note=note,
            user_id=user_id,
            user_login=user_login,
        )

    def update_gitlab_token(
        self,
        token_id: str,
        *,
        token: Optional[str] = None,
        note: Optional[str] = None,
        user_id: Optional[str] = None,
        user_login: Optional[str] = None,
    ) -> None:
        """Legacy wrapper - uses unified remote_tokens table."""
        self.update_remote_token(
            token_id,
            token=token,
            note=note,
            user_id=user_id,
            user_login=user_login,
        )

    def delete_gitlab_token(self, token_id: str) -> None:
        """Legacy wrapper - uses unified remote_tokens table."""
        self.delete_remote_token(token_id)

    # --- Unified RemoteToken methods ---

    def list_remote_tokens(
        self,
        provider: Optional[str] = None,
    ) -> list[RemoteTokenRecord]:
        with self.session() as session:
            stmt = select(RemoteTokenModel)
            if provider:
                stmt = stmt.where(RemoteTokenModel.provider == provider)
            rows = session.execute(stmt.order_by(RemoteTokenModel.created_at.desc())).scalars().all()
        return [
            RemoteTokenRecord(
                id=row.id,
                provider=row.provider,
                note=row.note,
                token=row.token,
                base_url=row.base_url,
                user_id=row.user_id,
                user_login=row.user_login,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_remote_token(self, token_id: str) -> Optional[RemoteTokenRecord]:
        with self.session() as session:
            row = session.get(RemoteTokenModel, token_id)
            if not row:
                return None
            return RemoteTokenRecord(
                id=row.id,
                provider=row.provider,
                note=row.note,
                token=row.token,
                base_url=row.base_url,
                user_id=row.user_id,
                user_login=row.user_login,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def insert_remote_token(
        self,
        token_id: str,
        *,
        provider: str,
        token: str,
        note: Optional[str] = None,
        base_url: Optional[str] = None,
        user_id: Optional[str] = None,
        user_login: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                RemoteTokenModel(
                    id=token_id,
                    provider=provider,
                    note=note,
                    token=token,
                    base_url=base_url,
                    user_id=user_id,
                    user_login=user_login,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_remote_token(
        self,
        token_id: str,
        *,
        provider: Optional[str] = None,
        token: Optional[str] = None,
        note: Optional[str] = None,
        base_url: Optional[str] = None,
        user_id: Optional[str] = None,
        user_login: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(RemoteTokenModel, token_id)
            if not row:
                return
            if provider is not None:
                row.provider = provider
            if token is not None:
                row.token = token
            if note is not None:
                row.note = note
            if base_url is not None:
                row.base_url = base_url or None
            if user_id is not None:
                row.user_id = user_id
            if user_login is not None:
                row.user_login = user_login
            row.updated_at = utc_now()

    def delete_remote_token(self, token_id: str) -> None:
        with self.session() as session:
            row = session.get(RemoteTokenModel, token_id)
            if row:
                session.query(IssueSourceModel).filter(
                    IssueSourceModel.token_id == token_id,
                    IssueSourceModel.provider == row.provider,
                ).delete()
                session.query(RemoteTokenModel).filter(RemoteTokenModel.id == token_id).delete()

    def list_issue_sources(
        self,
        project_id: Optional[str] = None,
        provider: Optional[str] = None,
    ) -> list[IssueSourceRecord]:
        with self.session() as session:
            stmt = select(IssueSourceModel)
            if project_id:
                stmt = stmt.where(IssueSourceModel.project_id == project_id)
            if provider:
                stmt = stmt.where(IssueSourceModel.provider == provider)
            rows = session.execute(stmt.order_by(IssueSourceModel.created_at.desc())).scalars().all()
        return [
            IssueSourceRecord(
                id=row.id,
                provider=row.provider,
                token_id=row.token_id,
                agent_id=row.agent_id,
                project_id=row.project_id,
                repo=row.repo,
                state=row.state,
                labels=json_loads(row.labels_json) or [],
                enabled=bool(row.enabled),
                auto_start=bool(row.auto_start),
                poll_interval_seconds=row.poll_interval_seconds,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_issue_source(self, source_id: str) -> Optional[IssueSourceRecord]:
        with self.session() as session:
            row = session.get(IssueSourceModel, source_id)
        if not row:
            return None
        return IssueSourceRecord(
            id=row.id,
            provider=row.provider,
            token_id=row.token_id,
            agent_id=row.agent_id,
            project_id=row.project_id,
            repo=row.repo,
            state=row.state,
            labels=json_loads(row.labels_json) or [],
            enabled=bool(row.enabled),
            auto_start=bool(row.auto_start),
            poll_interval_seconds=row.poll_interval_seconds,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_issue_source(
        self,
        source_id: str,
        provider: str,
        token_id: Optional[str],
        agent_id: Optional[str],
        project_id: str,
        repo: str,
        state: str,
        labels: list[str],
        enabled: bool,
        auto_start: bool,
        poll_interval_seconds: int = 60,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                IssueSourceModel(
                    id=source_id,
                    provider=provider,
                    token_id=token_id,
                    agent_id=agent_id,
                    project_id=project_id,
                    repo=repo,
                    state=state,
                    labels_json=json_dumps(labels),
                    enabled=1 if enabled else 0,
                    auto_start=1 if auto_start else 0,
                    poll_interval_seconds=poll_interval_seconds,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_issue_source(
        self,
        source_id: str,
        *,
        provider: Optional[str] = None,
        token_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        repo: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[list[str]] = None,
        enabled: Optional[bool] = None,
        auto_start: Optional[bool] = None,
        poll_interval_seconds: Optional[int] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(IssueSourceModel, source_id)
            if not row:
                return
            if provider is not None:
                row.provider = provider
            if token_id is not None:
                row.token_id = token_id
            if agent_id is not None:
                row.agent_id = agent_id
            if project_id is not None:
                row.project_id = project_id
            if repo is not None:
                row.repo = repo
            if state is not None:
                row.state = state
            if labels is not None:
                row.labels_json = json_dumps(labels)
            if enabled is not None:
                row.enabled = 1 if enabled else 0
            if auto_start is not None:
                row.auto_start = 1 if auto_start else 0
            if poll_interval_seconds is not None:
                row.poll_interval_seconds = poll_interval_seconds
            row.updated_at = utc_now()

    def delete_issue_source(self, source_id: str) -> None:
        with self.session() as session:
            session.query(IssueSourceModel).filter(IssueSourceModel.id == source_id).delete()

    def list_github_sources(self, project_id: Optional[str] = None) -> list[GitHubSourceRecord]:
        sources = self.list_issue_sources(project_id=project_id, provider="github")
        results: list[GitHubSourceRecord] = []
        for source in sources:
            owner, repo = _split_repo(source.repo)
            results.append(
                GitHubSourceRecord(
                    id=source.id,
                    token_id=source.token_id,
                    agent_id=source.agent_id,
                    project_id=source.project_id,
                    owner=owner,
                    repo=repo,
                    state=source.state,
                    labels=source.labels,
                    enabled=source.enabled,
                    auto_start=source.auto_start,
                    poll_interval_seconds=source.poll_interval_seconds,
                    created_at=source.created_at,
                    updated_at=source.updated_at,
                )
            )
        return results

    def get_github_source(self, source_id: str) -> Optional[GitHubSourceRecord]:
        source = self.get_issue_source(source_id)
        if not source or source.provider != "github":
            return None
        owner, repo = _split_repo(source.repo)
        return GitHubSourceRecord(
            id=source.id,
            token_id=source.token_id,
            agent_id=source.agent_id,
            project_id=source.project_id,
            owner=owner,
            repo=repo,
            state=source.state,
            labels=source.labels,
            enabled=source.enabled,
            auto_start=source.auto_start,
            poll_interval_seconds=source.poll_interval_seconds,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    def insert_github_source(
        self,
        source_id: str,
        token_id: Optional[str],
        agent_id: Optional[str],
        project_id: str,
        owner: str,
        repo: str,
        state: str,
        labels: list[str],
        enabled: bool,
        auto_start: bool,
    ) -> None:
        repo_value = f"{owner.strip()}/{repo.strip()}".strip("/")
        self.insert_issue_source(
            source_id,
            "github",
            token_id,
            agent_id,
            project_id,
            repo_value,
            state,
            labels,
            enabled,
            auto_start,
        )

    def update_github_source(
        self,
        source_id: str,
        *,
        token_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        owner: Optional[str] = None,
        repo: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[list[str]] = None,
        enabled: Optional[bool] = None,
        auto_start: Optional[bool] = None,
    ) -> None:
        repo_value = None
        if owner is not None or repo is not None:
            current = self.get_github_source(source_id)
            current_owner = owner if owner is not None else (current.owner if current else "")
            current_repo = repo if repo is not None else (current.repo if current else "")
            repo_value = f"{current_owner.strip()}/{current_repo.strip()}".strip("/")
        self.update_issue_source(
            source_id,
            token_id=token_id,
            agent_id=agent_id,
            project_id=project_id,
            repo=repo_value,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
        )

    def delete_github_source(self, source_id: str) -> None:
        self.delete_issue_source(source_id)

    def list_auto_start_tickets(self) -> list[TicketRecord]:
        with self.session() as session:
            rows = session.execute(
                select(TicketModel)
                .where(TicketModel.auto_start == 1)
                .where(TicketModel.status == "open")
                .order_by(TicketModel.updated_at.desc())
            ).scalars().all()
        return [
            TicketRecord(
                id=row.id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                sprint_id=row.sprint_id,
                title=row.title,
                description=row.description,
                internal_notes=row.internal_notes,
                assigned_to=row.assigned_to,
                estimate=row.estimate,
                hours=float(row.hours) if row.hours else None,
                story_points=float(row.story_points) if row.story_points else None,
                priority=row.priority,
                status=row.status,
                source_url=row.source_url,
                github_comments_json=row.github_comments_json,
                github_comments_fetched_at=row.github_comments_fetched_at,
                auto_start=bool(row.auto_start),
                created_by_id=row.created_by_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def list_gitlab_sources(self, project_id: Optional[str] = None) -> list[GitLabSourceRecord]:
        sources = self.list_issue_sources(project_id=project_id, provider="gitlab")
        return [
            GitLabSourceRecord(
                id=source.id,
                token_id=source.token_id,
                agent_id=source.agent_id,
                project_id=source.project_id,
                project_path=source.repo,
                state=source.state,
                labels=source.labels,
                enabled=source.enabled,
                auto_start=source.auto_start,
                poll_interval_seconds=source.poll_interval_seconds,
                created_at=source.created_at,
                updated_at=source.updated_at,
            )
            for source in sources
        ]

    def get_gitlab_source(self, source_id: str) -> Optional[GitLabSourceRecord]:
        source = self.get_issue_source(source_id)
        if not source or source.provider != "gitlab":
            return None
        return GitLabSourceRecord(
            id=source.id,
            token_id=source.token_id,
            agent_id=source.agent_id,
            project_id=source.project_id,
            project_path=source.repo,
            state=source.state,
            labels=source.labels,
            enabled=source.enabled,
            auto_start=source.auto_start,
            poll_interval_seconds=source.poll_interval_seconds,
            created_at=source.created_at,
            updated_at=source.updated_at,
        )

    def insert_gitlab_source(
        self,
        source_id: str,
        token_id: Optional[str],
        agent_id: Optional[str],
        project_id: str,
        project_path: str,
        state: str,
        labels: list[str],
        enabled: bool,
        auto_start: bool,
    ) -> None:
        self.insert_issue_source(
            source_id,
            "gitlab",
            token_id,
            agent_id,
            project_id,
            project_path.strip().strip("/"),
            state,
            labels,
            enabled,
            auto_start,
        )

    def update_gitlab_source(
        self,
        source_id: str,
        *,
        token_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        project_id: Optional[str] = None,
        project_path: Optional[str] = None,
        state: Optional[str] = None,
        labels: Optional[list[str]] = None,
        enabled: Optional[bool] = None,
        auto_start: Optional[bool] = None,
    ) -> None:
        repo_value = project_path.strip().strip("/") if project_path is not None else None
        self.update_issue_source(
            source_id,
            token_id=token_id,
            agent_id=agent_id,
            project_id=project_id,
            repo=repo_value,
            state=state,
            labels=labels,
            enabled=enabled,
            auto_start=auto_start,
        )

    def delete_gitlab_source(self, source_id: str) -> None:
        self.delete_issue_source(source_id)

    def list_tickets(self, project_id: Optional[str] = None, sprint_id: Optional[str] = None) -> list[TicketRecord]:
        with self.session() as session:
            stmt = select(TicketModel)
            if project_id:
                stmt = stmt.where(TicketModel.project_id == project_id)
            if sprint_id:
                stmt = stmt.where(TicketModel.sprint_id == sprint_id)
            rows = session.execute(stmt.order_by(TicketModel.created_at.desc())).scalars().all()
            return [
                TicketRecord(
                    id=row.id,
                    project_id=row.project_id,
                    agent_id=row.agent_id,
                    sprint_id=row.sprint_id,
                    title=row.title,
                    description=row.description,
                    internal_notes=row.internal_notes,
                    assigned_to=row.assigned_to,
                    estimate=row.estimate,
                    hours=float(row.hours) if row.hours else None,
                    story_points=float(row.story_points) if row.story_points else None,
                    priority=row.priority,
                    status=row.status,
                    source_url=row.source_url,
                    github_comments_json=row.github_comments_json,
                    github_comments_fetched_at=row.github_comments_fetched_at,
                    auto_start=bool(row.auto_start),
                    created_by_id=row.created_by_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    def get_ticket(self, ticket_id: str) -> Optional[TicketRecord]:
        with self.session() as session:
            row = session.get(TicketModel, ticket_id)
            if not row:
                return None
            return TicketRecord(
                id=row.id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                sprint_id=row.sprint_id,
                title=row.title,
                description=row.description,
                internal_notes=row.internal_notes,
                assigned_to=row.assigned_to,
                estimate=row.estimate,
                hours=float(row.hours) if row.hours else None,
                story_points=float(row.story_points) if row.story_points else None,
                priority=row.priority,
                status=row.status,
                source_url=row.source_url,
                github_comments_json=row.github_comments_json,
                github_comments_fetched_at=row.github_comments_fetched_at,
                auto_start=bool(row.auto_start),
                created_by_id=row.created_by_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def insert_ticket(
        self,
        ticket_id: str,
        project_id: str,
        title: str,
        description: Optional[str],
        assigned_to: Optional[str],
        estimate: Optional[str],
        status: str,
        internal_notes: Optional[str] = None,
        source_url: Optional[str] = None,
        agent_id: Optional[str] = None,
        sprint_id: Optional[str] = None,
        hours: Optional[float] = None,
        story_points: Optional[float] = None,
        priority: Optional[str] = None,
        auto_start: bool = False,
        created_by_id: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                TicketModel(
                    id=ticket_id,
                    project_id=project_id,
                    agent_id=agent_id,
                    sprint_id=sprint_id,
                    title=title,
                    description=description,
                    internal_notes=internal_notes,
                    assigned_to=assigned_to,
                    estimate=estimate,
                    hours=str(hours) if hours is not None else None,
                    story_points=str(story_points) if story_points is not None else None,
                    priority=priority,
                    status=status,
                    source_url=source_url,
                    github_comments_json=None,
                    github_comments_fetched_at=None,
                    auto_start=1 if auto_start else 0,
                    created_by_id=created_by_id,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_ticket(
        self,
        ticket_id: str,
        *,
        project_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        sprint_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        internal_notes: Optional[str] = None,
        assigned_to: Optional[str] = None,
        estimate: Optional[str] = None,
        hours: Optional[float] = None,
        story_points: Optional[float] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        source_url: Optional[str] = None,
        github_comments_json: Optional[str] = None,
        github_comments_fetched_at: Optional[str] = None,
        auto_start: Optional[bool] = None,
        clear_sprint: bool = False,
        clear_hours: bool = False,
        clear_story_points: bool = False,
        clear_priority: bool = False,
    ) -> None:
        with self.session() as session:
            row = session.get(TicketModel, ticket_id)
            if not row:
                return
            if project_id is not None:
                row.project_id = project_id
            if agent_id is not None:
                row.agent_id = agent_id or None
            if sprint_id is not None:
                row.sprint_id = sprint_id or None
            if clear_sprint:
                row.sprint_id = None
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description
            if internal_notes is not None:
                row.internal_notes = internal_notes
            if assigned_to is not None:
                row.assigned_to = assigned_to
            if estimate is not None:
                row.estimate = estimate
            if hours is not None:
                row.hours = str(hours)
            if clear_hours:
                row.hours = None
            if story_points is not None:
                row.story_points = str(story_points)
            if clear_story_points:
                row.story_points = None
            if priority is not None:
                row.priority = priority or None
            if clear_priority:
                row.priority = None
            if status is not None:
                row.status = status
            if source_url is not None:
                row.source_url = source_url
            if github_comments_json is not None:
                row.github_comments_json = github_comments_json
            if github_comments_fetched_at is not None:
                row.github_comments_fetched_at = github_comments_fetched_at
            if auto_start is not None:
                row.auto_start = 1 if auto_start else 0
            row.updated_at = utc_now()

    def backfill_ticket_source_urls(self) -> int:
        updated = 0
        api_base = os.environ.get("WINTERMUTE_GITLAB_API_BASE", "https://gitlab.com/api/v4").rstrip("/")
        gitlab_web_base = os.environ.get("WINTERMUTE_GITLAB_WEB_BASE_URL", "").strip()
        if not gitlab_web_base:
            gitlab_web_base = api_base[:-7] if api_base.endswith("/api/v4") else api_base
        with self.session() as session:
            rows = (
                session.execute(
                    select(TicketModel).where(
                        TicketModel.source_url.is_(None)
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                if row.id.startswith("github:"):
                    parts = row.id.split(":")
                    if len(parts) < 3:
                        continue
                    source_id = parts[1]
                    issue_number = parts[2]
                    source = session.get(IssueSourceModel, source_id)
                    if not source or source.provider != "github":
                        continue
                    owner, repo = _split_repo(source.repo)
                    if not owner or not repo:
                        continue
                    row.source_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"
                    row.updated_at = utc_now()
                    updated += 1
                elif row.id.startswith("gitlab:"):
                    parts = row.id.split(":")
                    if len(parts) < 3:
                        continue
                    source_id = parts[1]
                    issue_number = parts[2]
                    source = session.get(IssueSourceModel, source_id)
                    if not source or source.provider != "gitlab":
                        continue
                    row.source_url = f"{gitlab_web_base}/{source.repo}/-/issues/{issue_number}"
                    row.updated_at = utc_now()
                    updated += 1
        return updated

    def delete_ticket(self, ticket_id: str) -> None:
        with self.session() as session:
            session.query(TicketModel).filter(TicketModel.id == ticket_id).delete()

    # Sprint CRUD methods

    def list_sprints(self, status: Optional[str] = None) -> list[SprintRecord]:
        with self.session() as session:
            stmt = select(SprintModel)
            if status:
                stmt = stmt.where(SprintModel.status == status)
            rows = session.execute(stmt.order_by(SprintModel.start_date.desc())).scalars().all()
            return [
                SprintRecord(
                    id=row.id,
                    name=row.name,
                    start_date=row.start_date,
                    end_date=row.end_date,
                    enabled=bool(row.enabled),
                    status=row.status,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    def get_sprint(self, sprint_id: str) -> Optional[SprintRecord]:
        with self.session() as session:
            row = session.get(SprintModel, sprint_id)
            if not row:
                return None
            return SprintRecord(
                id=row.id,
                name=row.name,
                start_date=row.start_date,
                end_date=row.end_date,
                enabled=bool(row.enabled),
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def get_active_sprint(self) -> Optional[SprintRecord]:
        """Get the current active sprint."""
        with self.session() as session:
            row = session.execute(
                select(SprintModel)
                .where(SprintModel.status == "active")
                .order_by(SprintModel.start_date.desc())
            ).scalars().first()
            if not row:
                return None
            return SprintRecord(
                id=row.id,
                name=row.name,
                start_date=row.start_date,
                end_date=row.end_date,
                enabled=bool(row.enabled),
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    def insert_sprint(
        self,
        sprint_id: str,
        name: str,
        start_date: str,
        end_date: str,
        enabled: bool = True,
        status: str = "active",
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                SprintModel(
                    id=sprint_id,
                    name=name,
                    start_date=start_date,
                    end_date=end_date,
                    enabled=1 if enabled else 0,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_sprint(
        self,
        sprint_id: str,
        *,
        name: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        enabled: Optional[bool] = None,
        status: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(SprintModel, sprint_id)
            if not row:
                return
            if name is not None:
                row.name = name
            if start_date is not None:
                row.start_date = start_date
            if end_date is not None:
                row.end_date = end_date
            if enabled is not None:
                row.enabled = 1 if enabled else 0
            if status is not None:
                row.status = status
            row.updated_at = utc_now()

    def delete_sprint(self, sprint_id: str) -> None:
        with self.session() as session:
            session.query(SprintModel).filter(SprintModel.id == sprint_id).delete()

    def move_open_tickets_to_sprint(self, from_sprint_id: str, to_sprint_id: str) -> int:
        """Move all open tickets from one sprint to another. Returns count moved."""
        with self.session() as session:
            # Get open tickets in the source sprint via junction table
            ticket_ids = session.execute(
                select(TicketSprintModel.ticket_id)
                .where(TicketSprintModel.sprint_id == from_sprint_id)
            ).scalars().all()
            if not ticket_ids:
                return 0
            open_tickets = session.execute(
                select(TicketModel)
                .where(TicketModel.id.in_(ticket_ids))
                .where(TicketModel.status.in_(["open", "in-progress"]))
            ).scalars().all()
            count = 0
            now = utc_now()
            for ticket in open_tickets:
                # Remove from old sprint
                session.execute(
                    select(TicketSprintModel)
                    .where(TicketSprintModel.ticket_id == ticket.id)
                    .where(TicketSprintModel.sprint_id == from_sprint_id)
                )
                session.query(TicketSprintModel).filter(
                    TicketSprintModel.ticket_id == ticket.id,
                    TicketSprintModel.sprint_id == from_sprint_id,
                ).delete()
                # Add to new sprint
                session.add(TicketSprintModel(
                    ticket_id=ticket.id,
                    sprint_id=to_sprint_id,
                    created_at=now,
                ))
                count += 1
            return count

    def add_ticket_to_sprint(self, ticket_id: str, sprint_id: str) -> bool:
        """Add a ticket to a sprint. Returns True if added, False if already exists."""
        with self.session() as session:
            existing = session.execute(
                select(TicketSprintModel)
                .where(TicketSprintModel.ticket_id == ticket_id)
                .where(TicketSprintModel.sprint_id == sprint_id)
            ).scalars().first()
            if existing:
                return False
            session.add(TicketSprintModel(
                ticket_id=ticket_id,
                sprint_id=sprint_id,
                created_at=utc_now(),
            ))
            return True

    def remove_ticket_from_sprint(self, ticket_id: str, sprint_id: str) -> bool:
        """Remove a ticket from a sprint. Returns True if removed, False if not found."""
        with self.session() as session:
            deleted = session.query(TicketSprintModel).filter(
                TicketSprintModel.ticket_id == ticket_id,
                TicketSprintModel.sprint_id == sprint_id,
            ).delete()
            return deleted > 0

    def list_tickets_in_sprint(self, sprint_id: str) -> list[TicketRecord]:
        """List all tickets in a sprint via the junction table."""
        with self.session() as session:
            ticket_ids = session.execute(
                select(TicketSprintModel.ticket_id)
                .where(TicketSprintModel.sprint_id == sprint_id)
            ).scalars().all()
            if not ticket_ids:
                return []
            rows = session.execute(
                select(TicketModel)
                .where(TicketModel.id.in_(ticket_ids))
                .order_by(TicketModel.created_at.desc())
            ).scalars().all()
            return [
                TicketRecord(
                    id=row.id,
                    project_id=row.project_id,
                    agent_id=row.agent_id,
                    sprint_id=row.sprint_id,
                    title=row.title,
                    description=row.description,
                    internal_notes=row.internal_notes,
                    assigned_to=row.assigned_to,
                    estimate=row.estimate,
                    hours=float(row.hours) if row.hours else None,
                    story_points=float(row.story_points) if row.story_points else None,
                    priority=row.priority,
                    status=row.status,
                    source_url=row.source_url,
                    github_comments_json=row.github_comments_json,
                    github_comments_fetched_at=row.github_comments_fetched_at,
                    auto_start=bool(row.auto_start),
                    created_by_id=row.created_by_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    def list_tickets_not_in_sprint(self, sprint_id: str, status_filter: Optional[list[str]] = None) -> list[TicketRecord]:
        """List all tickets not in the given sprint, optionally filtered by status."""
        with self.session() as session:
            # Get ticket IDs already in this sprint
            in_sprint_ids = session.execute(
                select(TicketSprintModel.ticket_id)
                .where(TicketSprintModel.sprint_id == sprint_id)
            ).scalars().all()
            stmt = select(TicketModel)
            if in_sprint_ids:
                stmt = stmt.where(TicketModel.id.notin_(in_sprint_ids))
            if status_filter:
                stmt = stmt.where(TicketModel.status.in_(status_filter))
            rows = session.execute(
                stmt.order_by(TicketModel.created_at.desc())
            ).scalars().all()
            return [
                TicketRecord(
                    id=row.id,
                    project_id=row.project_id,
                    agent_id=row.agent_id,
                    sprint_id=row.sprint_id,
                    title=row.title,
                    description=row.description,
                    internal_notes=row.internal_notes,
                    assigned_to=row.assigned_to,
                    estimate=row.estimate,
                    hours=float(row.hours) if row.hours else None,
                    story_points=float(row.story_points) if row.story_points else None,
                    priority=row.priority,
                    status=row.status,
                    source_url=row.source_url,
                    github_comments_json=row.github_comments_json,
                    github_comments_fetched_at=row.github_comments_fetched_at,
                    auto_start=bool(row.auto_start),
                    created_by_id=row.created_by_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]

    def list_comments(self, ticket_id: Optional[str] = None) -> list[CommentRecord]:
        with self.session() as session:
            stmt = select(CommentModel)
            if ticket_id:
                stmt = stmt.where(CommentModel.ticket_id == ticket_id)
            rows = session.execute(stmt.order_by(CommentModel.created_at.desc())).scalars().all()
        return [
            CommentRecord(
                id=row.id,
                ticket_id=row.ticket_id,
                session_id=row.session_id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                author=row.author,
                source_id=row.source_id,
                issue_number=row.issue_number,
                body=row.body,
                public=bool(row.public),
                approved=bool(row.approved),
                sent=bool(row.sent),
                sent_at=row.sent_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def list_comments_since(self, ticket_id: str, since: Optional[str]) -> list[CommentRecord]:
        with self.session() as session:
            stmt = select(CommentModel).where(CommentModel.ticket_id == ticket_id)
            if since:
                stmt = stmt.where(CommentModel.created_at > since)
            rows = session.execute(stmt.order_by(CommentModel.created_at.asc())).scalars().all()
        return [
            CommentRecord(
                id=row.id,
                ticket_id=row.ticket_id,
                session_id=row.session_id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                author=row.author,
                source_id=row.source_id,
                issue_number=row.issue_number,
                body=row.body,
                public=bool(row.public),
                approved=bool(row.approved),
                sent=bool(row.sent),
                sent_at=row.sent_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_comment(self, comment_id: str) -> Optional[CommentRecord]:
        with self.session() as session:
            row = session.get(CommentModel, comment_id)
        if not row:
            return None
        return CommentRecord(
            id=row.id,
            ticket_id=row.ticket_id,
            session_id=row.session_id,
            project_id=row.project_id,
            agent_id=row.agent_id,
            author=row.author,
            source_id=row.source_id,
            issue_number=row.issue_number,
            body=row.body,
            public=bool(row.public),
            approved=bool(row.approved),
            sent=bool(row.sent),
            sent_at=row.sent_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_comment(
        self,
        comment_id: str,
        ticket_id: str,
        session_id: Optional[str],
        project_id: Optional[str],
        agent_id: Optional[str],
        author: Optional[str],
        source_id: Optional[str],
        issue_number: Optional[int],
        body: str,
        public: bool,
        approved: bool = False,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                CommentModel(
                    id=comment_id,
                    ticket_id=ticket_id,
                    session_id=session_id,
                    project_id=project_id,
                    agent_id=agent_id,
                    author=author,
                    source_id=source_id,
                    issue_number=issue_number,
                    body=body,
                    public=1 if public else 0,
                    approved=1 if approved else 0,
                    sent=0,
                    sent_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_comment(
        self,
        comment_id: str,
        *,
        body: Optional[str] = None,
        public: Optional[bool] = None,
        approved: Optional[bool] = None,
        sent: Optional[bool] = None,
        sent_at: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(CommentModel, comment_id)
            if not row:
                return
            if body is not None:
                row.body = body
            if public is not None:
                row.public = 1 if public else 0
            if approved is not None:
                row.approved = 1 if approved else 0
            if sent is not None:
                row.sent = 1 if sent else 0
            if sent_at is not None:
                row.sent_at = sent_at
            row.updated_at = utc_now()

    def delete_comment(self, comment_id: str) -> None:
        with self.session() as session:
            session.query(CommentModel).filter(CommentModel.id == comment_id).delete()

    def list_pending_comments(self) -> list[CommentRecord]:
        with self.session() as session:
            rows = (
                session.execute(
                    select(CommentModel)
                    .where(CommentModel.public == 1)
                    .where(CommentModel.approved == 1)
                    .where(CommentModel.sent == 0)
                    .order_by(CommentModel.created_at.asc())
                )
                .scalars()
                .all()
            )
        return [
            CommentRecord(
                id=row.id,
                ticket_id=row.ticket_id,
                session_id=row.session_id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                author=row.author,
                source_id=row.source_id,
                issue_number=row.issue_number,
                body=row.body,
                public=bool(row.public),
                approved=bool(row.approved),
                sent=bool(row.sent),
                sent_at=row.sent_at,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def mark_comment_sent(self, comment_id: str, sent_at: Optional[str] = None) -> None:
        with self.session() as session:
            row = session.get(CommentModel, comment_id)
            if not row:
                return
            row.sent = 1
            row.sent_at = sent_at or utc_now()
            row.updated_at = utc_now()

    def list_vm_targets(self) -> list[VMTargetRecord]:
        with self.session() as session:
            rows = session.execute(select(VMTargetModel).order_by(VMTargetModel.name)).scalars().all()
        return [
            VMTargetRecord(
                id=row.id,
                name=row.name,
                host=row.host,
                user=row.user,
                port=row.port,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def insert_vm_target(self, vm_id: str, name: str, host: str, user: str, port: int) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                VMTargetModel(
                    id=vm_id,
                    name=name,
                    host=host,
                    user=user,
                    port=port,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_vm_target(
        self,
        vm_id: str,
        *,
        name: Optional[str] = None,
        host: Optional[str] = None,
        user: Optional[str] = None,
        port: Optional[int] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(VMTargetModel, vm_id)
            if not row:
                return
            if name is not None:
                row.name = name
            if host is not None:
                row.host = host
            if user is not None:
                row.user = user
            if port is not None:
                row.port = port
            row.updated_at = utc_now()

    def delete_vm_target(self, vm_id: str) -> None:
        with self.session() as session:
            session.query(VMTargetModel).filter(VMTargetModel.id == vm_id).delete()

    def get_vm_target(self, vm_id: str) -> Optional[VMTargetRecord]:
        with self.session() as session:
            row = session.get(VMTargetModel, vm_id)
        if not row:
            return None
        return VMTargetRecord(
            id=row.id,
            name=row.name,
            host=row.host,
            user=row.user,
            port=row.port,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_agents(self) -> list[AgentRecord]:
        with self.session() as session:
            rows = session.execute(select(AgentModel).order_by(AgentModel.name)).scalars().all()
        return [
            AgentRecord(
                id=row.id,
                name=row.name,
                slug=row.slug,
                command=row.command,
                session_mode=row.session_mode,
                vm_target_id=row.vm_target_id,
                required_ssh_options=row.required_ssh_options,
                env_vars=row.env_vars,
                mcp_config=row.mcp_config,
                trust_level=row.trust_level,
                input_echo_prefix=row.input_echo_prefix,
                response_prefix=row.response_prefix,
                llm_base_url=row.llm_base_url,
                llm_api_key=row.llm_api_key,
                llm_model=row.llm_model,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def list_agent_responses(self, agent_id: Optional[str] = None) -> list[AgentResponseRecord]:
        with self.session() as session:
            stmt = select(AgentResponseModel)
            if agent_id:
                stmt = stmt.where(AgentResponseModel.agent_id == agent_id)
            rows = session.execute(stmt.order_by(AgentResponseModel.created_at.desc())).scalars().all()
        return [
            AgentResponseRecord(
                id=row.id,
                agent_id=row.agent_id,
                pattern=row.pattern,
                response=row.response,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_agent_response(self, response_id: str) -> Optional[AgentResponseRecord]:
        with self.session() as session:
            row = session.get(AgentResponseModel, response_id)
        if not row:
            return None
        return AgentResponseRecord(
            id=row.id,
            agent_id=row.agent_id,
            pattern=row.pattern,
            response=row.response,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_agent_response(
        self,
        response_id: str,
        agent_id: str,
        pattern: str,
        response: str,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                AgentResponseModel(
                    id=response_id,
                    agent_id=agent_id,
                    pattern=pattern,
                    response=response,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_agent_response(
        self,
        response_id: str,
        *,
        agent_id: Optional[str] = None,
        pattern: Optional[str] = None,
        response: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(AgentResponseModel, response_id)
            if not row:
                return
            if agent_id is not None:
                row.agent_id = agent_id
            if pattern is not None:
                row.pattern = pattern
            if response is not None:
                row.response = response
            row.updated_at = utc_now()

    def delete_agent_response(self, response_id: str) -> None:
        with self.session() as session:
            session.query(AgentResponseModel).filter(AgentResponseModel.id == response_id).delete()

    def insert_agent(
        self,
        agent_id: str,
        name: str,
        slug: str,
        command: str,
        session_mode: str,
        vm_target_id: Optional[str],
        required_ssh_options: Optional[str],
        env_vars: Optional[str],
        mcp_config: Optional[str],
        trust_level: Optional[str],
        input_echo_prefix: Optional[str],
        response_prefix: Optional[str],
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                AgentModel(
                    id=agent_id,
                    name=name,
                    slug=slug,
                    command=command,
                    session_mode=session_mode,
                    vm_target_id=vm_target_id,
                    required_ssh_options=required_ssh_options,
                    env_vars=env_vars,
                    mcp_config=mcp_config,
                    trust_level=trust_level,
                    input_echo_prefix=input_echo_prefix,
                    response_prefix=response_prefix,
                    llm_base_url=llm_base_url,
                    llm_api_key=llm_api_key,
                    llm_model=llm_model,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_agent(
        self,
        agent_id: str,
        *,
        name: Optional[str] = None,
        slug: Optional[str] = None,
        command: Optional[str] = None,
        session_mode: Optional[str] = None,
        vm_target_id: Optional[str] = None,
        required_ssh_options: Optional[str] = None,
        env_vars: Optional[str] = None,
        mcp_config: Optional[str] = None,
        trust_level: Optional[str] = None,
        input_echo_prefix: Optional[str] = None,
        response_prefix: Optional[str] = None,
        llm_base_url: Optional[str] = None,
        llm_api_key: Optional[str] = None,
        llm_model: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(AgentModel, agent_id)
            if not row:
                return
            if name is not None:
                row.name = name
            if slug is not None:
                row.slug = slug
            if command is not None:
                row.command = command
            if session_mode is not None:
                row.session_mode = session_mode
            if vm_target_id is not None:
                row.vm_target_id = vm_target_id
            if required_ssh_options is not None:
                row.required_ssh_options = required_ssh_options
            if env_vars is not None:
                row.env_vars = env_vars
            if mcp_config is not None:
                row.mcp_config = mcp_config
            if trust_level is not None:
                row.trust_level = trust_level
            if input_echo_prefix is not None:
                row.input_echo_prefix = input_echo_prefix
            if response_prefix is not None:
                row.response_prefix = response_prefix
            if llm_base_url is not None:
                row.llm_base_url = llm_base_url
            if llm_api_key is not None:
                row.llm_api_key = llm_api_key
            if llm_model is not None:
                row.llm_model = llm_model
            row.updated_at = utc_now()

    def delete_agent(self, agent_id: str) -> None:
        with self.session() as session:
            session.query(AgentModel).filter(AgentModel.id == agent_id).delete()

    def get_agent(self, agent_id: str) -> Optional[AgentRecord]:
        with self.session() as session:
            row = session.get(AgentModel, agent_id)
        if not row:
            return None
        return AgentRecord(
            id=row.id,
            name=row.name,
            slug=row.slug,
            command=row.command,
            session_mode=row.session_mode,
            vm_target_id=row.vm_target_id,
            required_ssh_options=row.required_ssh_options,
            env_vars=row.env_vars,
            mcp_config=row.mcp_config,
            trust_level=row.trust_level,
            input_echo_prefix=row.input_echo_prefix,
            response_prefix=row.response_prefix,
            llm_base_url=row.llm_base_url,
            llm_api_key=row.llm_api_key,
            llm_model=row.llm_model,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_agent_by_slug(self, slug: str) -> Optional[AgentRecord]:
        with self.session() as session:
            row = session.execute(select(AgentModel).where(AgentModel.slug == slug)).scalar_one_or_none()
        if not row:
            return None
        return AgentRecord(
            id=row.id,
            name=row.name,
            slug=row.slug,
            command=row.command,
            session_mode=row.session_mode,
            vm_target_id=row.vm_target_id,
            required_ssh_options=row.required_ssh_options,
            env_vars=row.env_vars,
            mcp_config=row.mcp_config,
            trust_level=row.trust_level,
            input_echo_prefix=row.input_echo_prefix,
            response_prefix=row.response_prefix,
            llm_base_url=row.llm_base_url,
            llm_api_key=row.llm_api_key,
            llm_model=row.llm_model,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_sessions(
        self, project_id: Optional[str] = None, status: Optional[str] = None
    ) -> list[AgentSessionRecord]:
        with self.session() as session:
            stmt = select(AgentSessionModel)
            if project_id:
                stmt = stmt.where(AgentSessionModel.project_id == project_id)
            if status:
                stmt = stmt.where(AgentSessionModel.status == status)
            rows = session.execute(stmt.order_by(AgentSessionModel.created_at.desc())).scalars().all()
        return [
            AgentSessionRecord(
                id=row.id,
                project_id=row.project_id,
                agent_id=row.agent_id,
                ticket_id=row.ticket_id,
                status=row.status,
                repo_path=row.repo_path,
                thread_ts=row.thread_ts,
                mcp_conversation_id=row.mcp_conversation_id,
                claude_session_id=getattr(row, "claude_session_id", None),
                last_output=row.last_output,
                last_output_offset=row.last_output_offset,
                output_buffer=row.output_buffer,
                output_buffer_updated_at=row.output_buffer_updated_at,
                prompt_pending=row.prompt_pending,
                prompt_sent_at=row.prompt_sent_at,
                last_output_at=row.last_output_at,
                awaiting_response=row.awaiting_response,
                last_user_message=row.last_user_message,
                queued_user_messages=row.queued_user_messages,
                awaiting_response_offset=row.awaiting_response_offset,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_session_by_ticket(self, ticket_id: str) -> Optional[AgentSessionRecord]:
        with self.session() as session:
            row = (
                session.execute(
                    select(AgentSessionModel)
                    .where(AgentSessionModel.ticket_id == ticket_id)
                    .order_by(AgentSessionModel.created_at.desc())
                )
                .scalars()
                .first()
            )
        if not row:
            return None
        return AgentSessionRecord(
            id=row.id,
            project_id=row.project_id,
            agent_id=row.agent_id,
            ticket_id=row.ticket_id,
            status=row.status,
            repo_path=row.repo_path,
            thread_ts=row.thread_ts,
            mcp_conversation_id=row.mcp_conversation_id,
            claude_session_id=getattr(row, "claude_session_id", None),
            last_output=row.last_output,
            last_output_offset=row.last_output_offset,
            output_buffer=row.output_buffer,
            output_buffer_updated_at=row.output_buffer_updated_at,
            prompt_pending=row.prompt_pending,
            prompt_sent_at=row.prompt_sent_at,
            last_output_at=row.last_output_at,
            awaiting_response=row.awaiting_response,
            last_user_message=row.last_user_message,
            queued_user_messages=row.queued_user_messages,
            awaiting_response_offset=row.awaiting_response_offset,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_session(
        self,
        session_id: str,
        project_id: str,
        agent_id: str,
        ticket_id: Optional[str],
        status: str,
        repo_path: str,
        thread_ts: Optional[str],
        mcp_conversation_id: Optional[str] = None,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                AgentSessionModel(
                    id=session_id,
                    project_id=project_id,
                    agent_id=agent_id,
                    ticket_id=ticket_id,
                    status=status,
                    repo_path=repo_path,
                    thread_ts=thread_ts,
                    mcp_conversation_id=mcp_conversation_id,
                    last_output=None,
                    last_output_offset=0,
                    output_buffer=None,
                    output_buffer_updated_at=None,
                    prompt_pending=None,
                    prompt_sent_at=None,
                    last_output_at=None,
                    awaiting_response=0,
                    last_user_message=None,
                    queued_user_messages=None,
                    awaiting_response_offset=0,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_session(
        self,
        session_id: str,
        *,
        status: Optional[str] = None,
        thread_ts: Optional[str] = None,
        mcp_conversation_id: Optional[str] = None,
        claude_session_id: Optional[str] = None,
        last_output: Optional[str] = None,
        last_output_offset: Optional[int] = None,
        output_buffer: Optional[str] = None,
        output_buffer_updated_at: Optional[str] = None,
        prompt_pending: Optional[str] = None,
        prompt_sent_at: Optional[str] = None,
        last_output_at: Optional[str] = None,
        awaiting_response: Optional[int] = None,
        last_user_message: Optional[str] = None,
        queued_user_messages: Optional[str] = None,
        awaiting_response_offset: Optional[int] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(AgentSessionModel, session_id)
            if not row:
                return
            if status is not None:
                row.status = status
            if thread_ts is not None:
                row.thread_ts = thread_ts
            if mcp_conversation_id is not None:
                row.mcp_conversation_id = mcp_conversation_id
            if claude_session_id is not None:
                row.claude_session_id = claude_session_id
            if last_output is not None:
                row.last_output = last_output
            if last_output_offset is not None:
                row.last_output_offset = last_output_offset
            if output_buffer is not None or output_buffer == "":
                row.output_buffer = output_buffer
            if output_buffer_updated_at is not None:
                row.output_buffer_updated_at = output_buffer_updated_at
            if prompt_pending is not None or prompt_pending == "":
                row.prompt_pending = prompt_pending
            if prompt_sent_at is not None:
                row.prompt_sent_at = prompt_sent_at
            if last_output_at is not None:
                row.last_output_at = last_output_at
            if awaiting_response is not None:
                row.awaiting_response = awaiting_response
            if last_user_message is not None or last_user_message == "":
                row.last_user_message = last_user_message
            if queued_user_messages is not None or queued_user_messages == "":
                row.queued_user_messages = queued_user_messages
            if awaiting_response_offset is not None:
                row.awaiting_response_offset = awaiting_response_offset
            row.updated_at = utc_now()

    def delete_session(self, session_id: str) -> None:
        with self.session() as session:
            session.query(AgentSessionModel).filter(AgentSessionModel.id == session_id).delete()

    def get_session_by_thread(self, thread_ts: str) -> Optional[AgentSessionRecord]:
        with self.session() as session:
            row = (
                session.execute(
                    select(AgentSessionModel).where(AgentSessionModel.thread_ts == thread_ts)
                )
                .scalar_one_or_none()
            )
        if not row:
            return None
        return AgentSessionRecord(
            id=row.id,
            project_id=row.project_id,
            agent_id=row.agent_id,
            ticket_id=row.ticket_id,
            status=row.status,
            repo_path=row.repo_path,
            thread_ts=row.thread_ts,
            mcp_conversation_id=row.mcp_conversation_id,
            claude_session_id=getattr(row, "claude_session_id", None),
            last_output=row.last_output,
            last_output_offset=row.last_output_offset,
            output_buffer=row.output_buffer,
            output_buffer_updated_at=row.output_buffer_updated_at,
            prompt_pending=row.prompt_pending,
            prompt_sent_at=row.prompt_sent_at,
            last_output_at=row.last_output_at,
            awaiting_response=row.awaiting_response,
            last_user_message=row.last_user_message,
            queued_user_messages=row.queued_user_messages,
            awaiting_response_offset=row.awaiting_response_offset,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_session(self, session_id: str) -> Optional[AgentSessionRecord]:
        with self.session() as session:
            row = session.get(AgentSessionModel, session_id)
        if not row:
            return None
        return AgentSessionRecord(
            id=row.id,
            project_id=row.project_id,
            agent_id=row.agent_id,
            ticket_id=row.ticket_id,
            status=row.status,
            repo_path=row.repo_path,
            thread_ts=row.thread_ts,
            mcp_conversation_id=row.mcp_conversation_id,
            claude_session_id=getattr(row, "claude_session_id", None),
            last_output=row.last_output,
            last_output_offset=row.last_output_offset,
            output_buffer=row.output_buffer,
            output_buffer_updated_at=row.output_buffer_updated_at,
            prompt_pending=row.prompt_pending,
            prompt_sent_at=row.prompt_sent_at,
            last_output_at=row.last_output_at,
            awaiting_response=row.awaiting_response,
            last_user_message=row.last_user_message,
            queued_user_messages=row.queued_user_messages,
            awaiting_response_offset=row.awaiting_response_offset,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _model_to_work_item(self, row: WorkItemModel) -> WorkItemRecord:
        return WorkItemRecord(
            work_id=row.work_id,
            source_id=row.source_id,
            priority=row.priority,
            status=row.status,
            checkpoint=json_loads(row.checkpoint_json),
            created_at=row.created_at,
            updated_at=row.updated_at,
            run_after=row.run_after,
            attempts=row.attempts,
            last_error=row.last_error,
            last_traceback=row.last_traceback,
        )
