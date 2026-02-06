"""Django models for Wintermute."""

# Supervisor models
from .supervisor import (
    TaskSource,
    WorkItem,
    WorkItemRun,
    SupervisorState,
)

# Auth models
from .auth import (
    User,
    ColumnPreference,
    Credential,
)

# Project models
from .projects import (
    Project,
    Sprint,
    TicketSprint,
    Ticket,
    TicketHistory,
    Comment,
    RepoResource,
)

# Infrastructure models
from .infrastructure import (
    VMTarget,
)

# Source models
from .sources import (
    IssueSource,
    RemoteToken,
)

# Agent models
from .agents import (
    Agent,
    MetricDefinition,
    AgentMetricsLog,
    AgentSession,
    AgentResponse,
)

# Session models
from .sessions import (
    SessionFileConfig,
    SessionFileDefinition,
    SessionFile,
    Channel,
    AgentWake,
)

__all__ = [
    # Supervisor
    "TaskSource",
    "WorkItem",
    "WorkItemRun",
    "SupervisorState",
    # Auth
    "User",
    "ColumnPreference",
    "Credential",
    # Projects
    "Project",
    "Sprint",
    "TicketSprint",
    "Ticket",
    "TicketHistory",
    "Comment",
    "RepoResource",
    # Infrastructure
    "VMTarget",
    # Sources
    "IssueSource",
    "RemoteToken",
    # Agents
    "Agent",
    "MetricDefinition",
    "AgentMetricsLog",
    "AgentSession",
    "AgentResponse",
    # Sessions
    "SessionFileConfig",
    "SessionFileDefinition",
    "SessionFile",
    "Channel",
    "AgentWake",
]
