"""Agent and metrics-related models for Wintermute."""

import uuid
from django.conf import settings
from django.db import models
from rest_framework.authtoken.models import Token

from .infrastructure import VMTarget


def generate_uuid():
    return str(uuid.uuid4())


class Agent(models.Model):
    """CLI agent definition with command and configuration."""

    SESSION_MODE_CHOICES = [
        ("tmux", "tmux - Interactive terminal session"),
        ("mcp", "mcp - Codex MCP stdio client"),
        ("claude", "claude - Claude Code CLI"),
        ("gemini", "gemini - Gemini CLI"),
    ]

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, unique=True)
    command = models.CharField(max_length=255)
    session_mode = models.CharField(max_length=255, default="tmux", choices=SESSION_MODE_CHOICES)
    vm_target = models.ForeignKey(
        VMTarget,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        db_column='vm_target_id',
        related_name='agents',
        help_text="VM target where this agent runs"
    )
    required_ssh_options = models.TextField(null=True, blank=True)
    env_vars = models.TextField(null=True, blank=True)
    mcp_config = models.TextField(null=True, blank=True)
    trust_level = models.CharField(max_length=255, null=True, blank=True)
    input_echo_prefix = models.CharField(max_length=255, null=True, blank=True)
    response_prefix = models.CharField(max_length=255, null=True, blank=True)
    llm_base_url = models.CharField(max_length=255, null=True, blank=True)
    llm_api_key = models.CharField(max_length=255, null=True, blank=True)
    llm_model = models.CharField(max_length=255, null=True, blank=True)
    session_file_config_id = models.CharField(max_length=255, null=True, blank=True)
    average_memory_usage_mb = models.IntegerField(default=1000)
    initial_prompt = models.TextField(null=True, blank=True)
    working_directory = models.CharField(max_length=255, null=True, blank=True)
    session_directory = models.CharField(max_length=255, null=True, blank=True)
    autostart = models.BooleanField(default=False)
    health_command = models.TextField(null=True, blank=True)
    api_token = models.ForeignKey(
        Token, on_delete=models.SET_NULL, null=True, blank=True, related_name='agents', help_text="API token for this agent to authenticate with Wintermute"
    )
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "agents"
        verbose_name = "Agent"
        verbose_name_plural = "Agents"

    def __str__(self):
        return self.name


class MetricDefinition(models.Model):
    """Definition of a metric type that can be collected."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    metric_type = models.CharField(max_length=255, unique=True)
    recording_frequency_minutes = models.IntegerField(default=5)
    enabled = models.IntegerField(default=1) # SQLAlchemy used Integer for bool
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "metric_definitions"
        verbose_name = "Metric Definition"
        verbose_name_plural = "Metric Definitions"

    def __str__(self):
        return self.metric_type


class AgentMetricsLog(models.Model):
    """Time-series log of agent metric values."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    agent_id = models.CharField(max_length=255)
    metric_definition_id = models.CharField(max_length=255)
    value = models.FloatField()
    recorded_at = models.CharField(max_length=255) # ISO datetime string
    created_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "agent_metrics_logs"
        indexes = [
            models.Index(fields=["agent_id"], name="ix_aml_agent_id"),
            models.Index(fields=["metric_definition_id"], name="ix_aml_metric_def_id"),
            models.Index(fields=["recorded_at"], name="ix_aml_recorded_at"),
            models.Index(fields=["agent_id", "metric_definition_id"], name="ix_aml_agent_metric"),
        ]
        verbose_name = "Agent Metrics Log"
        verbose_name_plural = "Agent Metrics Logs"

    def __str__(self):
        return f"{self.agent_id}:{self.metric_definition_id} @ {self.recorded_at}"


class AgentSession(models.Model):
    """Persistent agent session (tmux or MCP) for a project VM."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    project_id = models.CharField(max_length=255, null=True, blank=True)
    agent_id = models.CharField(max_length=255)
    ticket_id = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=255)
    repo_path = models.CharField(max_length=255)
    thread_ts = models.CharField(max_length=255, null=True, blank=True)
    mcp_conversation_id = models.CharField(max_length=255, null=True, blank=True)
    claude_session_id = models.CharField(max_length=255, null=True, blank=True)
    last_output = models.TextField(null=True, blank=True)
    last_output_offset = models.IntegerField()
    output_buffer = models.TextField(null=True, blank=True)
    output_buffer_updated_at = models.CharField(max_length=255, null=True, blank=True)
    prompt_pending = models.TextField(null=True, blank=True)
    prompt_sent_at = models.CharField(max_length=255, null=True, blank=True)
    last_output_at = models.CharField(max_length=255, null=True, blank=True)
    awaiting_response = models.IntegerField(default=0) # SQLAlchemy used Integer for bool
    last_user_message = models.TextField(null=True, blank=True)
    queued_user_messages = models.TextField(null=True, blank=True)
    awaiting_response_offset = models.IntegerField(default=0)
    initial_prompt = models.TextField(null=True, blank=True)
    workspace_path = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "agent_sessions"
        verbose_name = "Agent Session"
        verbose_name_plural = "Agent Sessions"

    def __str__(self):
        return f"{self.agent_id} session {self.id}"


class AgentResponse(models.Model):
    """Pattern-based response configuration for agents."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    agent_id = models.CharField(max_length=255)
    pattern = models.TextField()
    response = models.TextField()
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "agent_responses"
        verbose_name = "Agent Response"
        verbose_name_plural = "Agent Responses"

    def __str__(self):
        return f"Response for {self.agent_id}: {self.pattern[:50]}"
