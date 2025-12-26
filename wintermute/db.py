"""SQLite persistence for Foreman using SQLAlchemy ORM."""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Generator, Optional

from sqlalchemy import Integer, String, Text, create_engine, inspect, select
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


@dataclass(frozen=True)
class CredentialRecord:
    id: str
    name: str
    provider: str
    reference: str
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
class ProjectRecord:
    id: str
    name: str
    slug: str
    slack_channel_id: Optional[str]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class TicketRecord:
    id: str
    project_id: str
    title: str
    description: Optional[str]
    assigned_to: Optional[str]
    estimate: Optional[str]
    status: str
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
    created_at: Mapped[str] = mapped_column(String, nullable=False)


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    salt: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[str] = mapped_column(String, nullable=False)


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
    assigned_to: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    estimate: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False)
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
                created_at=row.created_at,
            )
            for row in rows
        ]

    def insert_credential(self, cred_id: str, name: str, provider: str, reference: str) -> None:
        with self.session() as session:
            session.add(
                CredentialModel(
                    id=cred_id,
                    name=name,
                    provider=provider,
                    reference=reference,
                    created_at=utc_now(),
                )
            )

    def upsert_credential(self, cred_id: str, name: str, provider: str, reference: str) -> None:
        with self.session() as session:
            existing = session.get(CredentialModel, cred_id)
            if existing is None:
                session.add(
                    CredentialModel(
                        id=cred_id,
                        name=name,
                        provider=provider,
                        reference=reference,
                        created_at=utc_now(),
                    )
                )
                return
            existing.name = name
            existing.provider = provider
            existing.reference = reference

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
            session.query(TicketModel).filter(TicketModel.project_id == project_id).delete()
            session.query(ProjectModel).filter(ProjectModel.id == project_id).delete()

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
                assigned_to=row.assigned_to,
                estimate=row.estimate,
                status=row.status,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def insert_ticket(
        self,
        ticket_id: str,
        project_id: str,
        title: str,
        description: Optional[str],
        assigned_to: Optional[str],
        estimate: Optional[str],
        status: str,
    ) -> None:
        now = utc_now()
        with self.session() as session:
            session.add(
                TicketModel(
                    id=ticket_id,
                    project_id=project_id,
                    title=title,
                    description=description,
                    assigned_to=assigned_to,
                    estimate=estimate,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
            )

    def update_ticket(
        self,
        ticket_id: str,
        *,
        title: Optional[str] = None,
        description: Optional[str] = None,
        assigned_to: Optional[str] = None,
        estimate: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(TicketModel, ticket_id)
            if not row:
                return
            if title is not None:
                row.title = title
            if description is not None:
                row.description = description
            if assigned_to is not None:
                row.assigned_to = assigned_to
            if estimate is not None:
                row.estimate = estimate
            if status is not None:
                row.status = status
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
        repo_mode: Optional[str] = None,
        repo_path: Optional[str] = None,
        repo_url: Optional[str] = None,
    ) -> None:
        with self.session() as session:
            row = session.get(ProjectVMModel, project_vm_id)
            if not row:
                return
            if repo_mode is not None:
                row.repo_mode = repo_mode
            if repo_path is not None:
                row.repo_path = repo_path
            if repo_url is not None:
                row.repo_url = repo_url
            row.updated_at = utc_now()

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
        )
