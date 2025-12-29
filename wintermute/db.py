"""SQLite persistence for Foreman using SQLAlchemy ORM."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from sqlalchemy import Integer, String, Text, create_engine, func, inspect, select
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
class TicketRecord:
    id: str
    project_id: str
    title: str
    description: Optional[str]
    internal_notes: Optional[str]
    assigned_to: Optional[str]
    estimate: Optional[str]
    status: str
    source_url: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class CommentRecord:
    id: str
    ticket_id: str
    session_id: Optional[str]
    project_id: Optional[str]
    agent_id: Optional[str]
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
class VMTargetRecord:
    id: str
    name: str
    host: str
    user: str
    port: int
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ProjectVMRecord:
    id: str
    project_id: str
    vm_target_id: str
    repo_mode: str
    repo_path: Optional[str]
    repo_url: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentRecord:
    id: str
    name: str
    slug: str
    command: str
    required_ssh_options: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class AgentSessionRecord:
    id: str
    project_id: str
    project_vm_id: str
    agent_id: str
    ticket_id: Optional[str]
    status: str
    repo_path: str
    thread_ts: Optional[str]
    last_output: Optional[str]
    last_output_offset: int
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
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class TicketModel(Base):
    __tablename__ = "tickets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    internal_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    assigned_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    estimate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class CommentModel(Base):
    __tablename__ = "comments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    project_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    source_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    issue_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    public: Mapped[int] = mapped_column(Integer, nullable=False)
    approved: Mapped[int] = mapped_column(Integer, nullable=False)
    sent: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_at: Mapped[Optional[str]] = mapped_column(String, nullable=True)
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


class ProjectVMModel(Base):
    __tablename__ = "project_vms"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    vm_target_id: Mapped[str] = mapped_column(String, nullable=False)
    repo_mode: Mapped[str] = mapped_column(String, nullable=False)
    repo_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    repo_url: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class GitHubSourceModel(Base):
    __tablename__ = "github_sources"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    token_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    agent_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    owner: Mapped[str] = mapped_column(String, nullable=False)
    repo: Mapped[str] = mapped_column(String, nullable=False)
    state: Mapped[str] = mapped_column(String, nullable=False)
    labels_json: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[int] = mapped_column(Integer, nullable=False)
    auto_start: Mapped[int] = mapped_column(Integer, nullable=False)
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


class AgentModel(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    command: Mapped[str] = mapped_column(String, nullable=False)
    required_ssh_options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)


class AgentSessionModel(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, nullable=False)
    project_vm_id: Mapped[str] = mapped_column(String, nullable=False)
    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    ticket_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
    repo_path: Mapped[str] = mapped_column(String, nullable=False)
    thread_ts: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    last_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_output_offset: Mapped[int] = mapped_column(Integer, nullable=False)
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
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_project(
        self,
        project_id: str,
        name: str,
        slug: str,
        slack_channel_id: Optional[str],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                ProjectModel(
                    id=project_id,
                    name=name,
                    slug=slug,
                    slack_channel_id=slack_channel_id,
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
            row.updated_at = utc_now()

    def delete_project(self, project_id: str) -> None:
        with self.session() as session:
            session.query(AgentSessionModel).filter(
                AgentSessionModel.project_id == project_id
            ).delete()
            session.query(ProjectVMModel).filter(
                ProjectVMModel.project_id == project_id
            ).delete()
            session.query(GitHubSourceModel).filter(
                GitHubSourceModel.project_id == project_id
            ).delete()
            session.query(TicketModel).filter(TicketModel.project_id == project_id).delete()
            session.query(ProjectModel).filter(ProjectModel.id == project_id).delete()

    def list_github_tokens(self) -> list[GitHubTokenRecord]:
        with self.session() as session:
            rows = session.execute(select(GitHubTokenModel).order_by(GitHubTokenModel.created_at.desc())).scalars().all()
        return [
            GitHubTokenRecord(
                id=row.id,
                note=row.note,
                token=row.token,
                user_id=row.user_id,
                user_login=row.user_login,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_latest_github_token_update(self) -> str:
        with self.session() as session:
            value = session.execute(select(func.max(GitHubTokenModel.updated_at))).scalar_one_or_none()
        return value or ""

    def get_github_token(self, token_id: str) -> Optional[GitHubTokenRecord]:
        with self.session() as session:
            row = session.get(GitHubTokenModel, token_id)
        if not row:
            return None
        return GitHubTokenRecord(
            id=row.id,
            note=row.note,
            token=row.token,
            user_id=row.user_id,
            user_login=row.user_login,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_github_token(
        self,
        token_id: str,
        token: str,
        note: Optional[str],
        user_id: Optional[str],
        user_login: Optional[str],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                GitHubTokenModel(
                    id=token_id,
                    token=token,
                    note=note,
                    user_id=user_id,
                    user_login=user_login,
                    created_at=now,
                    updated_at=now,
                )
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
        with self.session() as session:
            row = session.get(GitHubTokenModel, token_id)
            if not row:
                return
            if token is not None:
                row.token = token
            if note is not None:
                row.note = note
            if user_id is not None:
                row.user_id = user_id
            if user_login is not None:
                row.user_login = user_login
            row.updated_at = utc_now()

    def delete_github_token(self, token_id: str) -> None:
        with self.session() as session:
            session.query(GitHubSourceModel).filter(
                GitHubSourceModel.token_id == token_id
            ).delete()
            session.query(GitHubTokenModel).filter(GitHubTokenModel.id == token_id).delete()

    def list_github_sources(self, project_id: Optional[str] = None) -> list[GitHubSourceRecord]:
        with self.session() as session:
            stmt = select(GitHubSourceModel)
            if project_id:
                stmt = stmt.where(GitHubSourceModel.project_id == project_id)
            rows = session.execute(stmt.order_by(GitHubSourceModel.created_at.desc())).scalars().all()
        return [
            GitHubSourceRecord(
                id=row.id,
                token_id=row.token_id,
                agent_id=row.agent_id,
                project_id=row.project_id,
                owner=row.owner,
                repo=row.repo,
                state=row.state,
                labels=json_loads(row.labels_json) or [],
                enabled=bool(row.enabled),
                auto_start=bool(row.auto_start),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_github_source(self, source_id: str) -> Optional[GitHubSourceRecord]:
        with self.session() as session:
            row = session.get(GitHubSourceModel, source_id)
        if not row:
            return None
        return GitHubSourceRecord(
            id=row.id,
            token_id=row.token_id,
            agent_id=row.agent_id,
            project_id=row.project_id,
            owner=row.owner,
            repo=row.repo,
            state=row.state,
            labels=json_loads(row.labels_json) or [],
            enabled=bool(row.enabled),
            auto_start=bool(row.auto_start),
            created_at=row.created_at,
            updated_at=row.updated_at,
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
        now = utc_now()
        with self.session() as session:
            session.add(
                GitHubSourceModel(
                    id=source_id,
                    token_id=token_id,
                    agent_id=agent_id,
                    project_id=project_id,
                    owner=owner,
                    repo=repo,
                    state=state,
                    labels_json=json_dumps(labels),
                    enabled=1 if enabled else 0,
                    auto_start=1 if auto_start else 0,
                    created_at=now,
                    updated_at=now,
                )
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
        with self.session() as session:
            row = session.get(GitHubSourceModel, source_id)
            if not row:
                return
            if token_id is not None:
                row.token_id = token_id
            if agent_id is not None:
                row.agent_id = agent_id
            if project_id is not None:
                row.project_id = project_id
            if owner is not None:
                row.owner = owner
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
            row.updated_at = utc_now()

    def delete_github_source(self, source_id: str) -> None:
        with self.session() as session:
            session.query(GitHubSourceModel).filter(GitHubSourceModel.id == source_id).delete()

    def list_tickets(self, project_id: Optional[str] = None) -> list[TicketRecord]:
        with self.session() as session:
            stmt = select(TicketModel)
            if project_id:
                stmt = stmt.where(TicketModel.project_id == project_id)
            rows = session.execute(stmt.order_by(TicketModel.created_at.desc())).scalars().all()
        return [
            TicketRecord(
                id=row.id,
                project_id=row.project_id,
                title=row.title,
                description=row.description,
                internal_notes=row.internal_notes,
                assigned_to=row.assigned_to,
                estimate=row.estimate,
                status=row.status,
                source_url=row.source_url,
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
            title=row.title,
            description=row.description,
            internal_notes=row.internal_notes,
            assigned_to=row.assigned_to,
            estimate=row.estimate,
            status=row.status,
            source_url=row.source_url,
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
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                TicketModel(
                    id=ticket_id,
                    project_id=project_id,
                    title=title,
                    description=description,
                    internal_notes=internal_notes,
                    assigned_to=assigned_to,
                    estimate=estimate,
                    status=status,
                    source_url=source_url,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_ticket(
        self,
        ticket_id: str,
        *,
        project_id: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        internal_notes: Optional[str] = None,
        assigned_to: Optional[str] = None,
        estimate: Optional[str] = None,
        status: Optional[str] = None,
        source_url: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(TicketModel, ticket_id)
            if not row:
                return
            if project_id is not None:
                row.project_id = project_id
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
            if status is not None:
                row.status = status
            if source_url is not None:
                row.source_url = source_url
            row.updated_at = utc_now()

    def delete_ticket(self, ticket_id: str) -> None:
        with self.session() as session:
            session.query(TicketModel).filter(TicketModel.id == ticket_id).delete()

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

    def list_project_vms(self, project_id: Optional[str] = None) -> list[ProjectVMRecord]:
        with self.session() as session:
            stmt = select(ProjectVMModel)
            if project_id:
                stmt = stmt.where(ProjectVMModel.project_id == project_id)
            rows = session.execute(stmt.order_by(ProjectVMModel.created_at.desc())).scalars().all()
        return [
            ProjectVMRecord(
                id=row.id,
                project_id=row.project_id,
                vm_target_id=row.vm_target_id,
                repo_mode=row.repo_mode,
                repo_path=row.repo_path,
                repo_url=row.repo_url,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def insert_project_vm(
        self,
        project_vm_id: str,
        project_id: str,
        vm_target_id: str,
        repo_mode: str,
        repo_path: Optional[str],
        repo_url: Optional[str],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                ProjectVMModel(
                    id=project_vm_id,
                    project_id=project_id,
                    vm_target_id=vm_target_id,
                    repo_mode=repo_mode,
                    repo_path=repo_path,
                    repo_url=repo_url,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_project_vm(
        self,
        project_vm_id: str,
        *,
        project_id: Optional[str] = None,
        vm_target_id: Optional[str] = None,
        repo_mode: Optional[str] = None,
        repo_path: Optional[str] = None,
        repo_url: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(ProjectVMModel, project_vm_id)
            if not row:
                return
            if project_id is not None:
                row.project_id = project_id
            if vm_target_id is not None:
                row.vm_target_id = vm_target_id
            if repo_mode is not None:
                row.repo_mode = repo_mode
            if repo_path is not None:
                row.repo_path = repo_path
            if repo_url is not None:
                row.repo_url = repo_url
            row.updated_at = utc_now()

    def delete_project_vm(self, project_vm_id: str) -> None:
        with self.session() as session:
            session.query(ProjectVMModel).filter(ProjectVMModel.id == project_vm_id).delete()

    def get_project_vm(self, project_vm_id: str) -> Optional[ProjectVMRecord]:
        with self.session() as session:
            row = session.get(ProjectVMModel, project_vm_id)
        if not row:
            return None
        return ProjectVMRecord(
            id=row.id,
            project_id=row.project_id,
            vm_target_id=row.vm_target_id,
            repo_mode=row.repo_mode,
            repo_path=row.repo_path,
            repo_url=row.repo_url,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def get_project_vm_for_project(self, project_id: str) -> Optional[ProjectVMRecord]:
        with self.session() as session:
            row = session.execute(
                select(ProjectVMModel)
                .where(ProjectVMModel.project_id == project_id)
                .order_by(ProjectVMModel.created_at.asc())
            ).scalars().first()
        if not row:
            return None
        return ProjectVMRecord(
            id=row.id,
            project_id=row.project_id,
            vm_target_id=row.vm_target_id,
            repo_mode=row.repo_mode,
            repo_path=row.repo_path,
            repo_url=row.repo_url,
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
                required_ssh_options=row.required_ssh_options,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def insert_agent(
        self,
        agent_id: str,
        name: str,
        slug: str,
        command: str,
        required_ssh_options: Optional[str],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                AgentModel(
                    id=agent_id,
                    name=name,
                    slug=slug,
                    command=command,
                    required_ssh_options=required_ssh_options,
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
        required_ssh_options: Optional[str] = None,
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
            if required_ssh_options is not None:
                row.required_ssh_options = required_ssh_options
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
            required_ssh_options=row.required_ssh_options,
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
            required_ssh_options=row.required_ssh_options,
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
                project_vm_id=row.project_vm_id,
                agent_id=row.agent_id,
                ticket_id=row.ticket_id,
                status=row.status,
                repo_path=row.repo_path,
                thread_ts=row.thread_ts,
                last_output=row.last_output,
                last_output_offset=row.last_output_offset,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def get_session_by_ticket(self, ticket_id: str) -> Optional[AgentSessionRecord]:
        with self.session() as session:
            row = session.execute(
                select(AgentSessionModel).where(AgentSessionModel.ticket_id == ticket_id)
            ).scalar_one_or_none()
        if not row:
            return None
        return AgentSessionRecord(
            id=row.id,
            project_id=row.project_id,
            project_vm_id=row.project_vm_id,
            agent_id=row.agent_id,
            ticket_id=row.ticket_id,
            status=row.status,
            repo_path=row.repo_path,
            thread_ts=row.thread_ts,
            last_output=row.last_output,
            last_output_offset=row.last_output_offset,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def insert_session(
        self,
        session_id: str,
        project_id: str,
        project_vm_id: str,
        agent_id: str,
        ticket_id: Optional[str],
        status: str,
        repo_path: str,
        thread_ts: Optional[str],
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                AgentSessionModel(
                    id=session_id,
                    project_id=project_id,
                    project_vm_id=project_vm_id,
                    agent_id=agent_id,
                    ticket_id=ticket_id,
                    status=status,
                    repo_path=repo_path,
                    thread_ts=thread_ts,
                    last_output=None,
                    last_output_offset=0,
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
        last_output: Optional[str] = None,
        last_output_offset: Optional[int] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(AgentSessionModel, session_id)
            if not row:
                return
            if status is not None:
                row.status = status
            if thread_ts is not None:
                row.thread_ts = thread_ts
            if last_output is not None:
                row.last_output = last_output
            if last_output_offset is not None:
                row.last_output_offset = last_output_offset
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
            project_vm_id=row.project_vm_id,
            agent_id=row.agent_id,
            ticket_id=row.ticket_id,
            status=row.status,
            repo_path=row.repo_path,
            thread_ts=row.thread_ts,
            last_output=row.last_output,
            last_output_offset=row.last_output_offset,
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
            project_vm_id=row.project_vm_id,
            agent_id=row.agent_id,
            ticket_id=row.ticket_id,
            status=row.status,
            repo_path=row.repo_path,
            thread_ts=row.thread_ts,
            last_output=row.last_output,
            last_output_offset=row.last_output_offset,
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
