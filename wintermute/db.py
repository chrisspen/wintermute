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
