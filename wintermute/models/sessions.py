"""Session file and channel models for Wintermute."""

import uuid
from django.db import models


def generate_uuid():
    return str(uuid.uuid4())


class SessionFileConfig(models.Model):
    """Configuration set for agent session files."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "session_file_configs"
        verbose_name = "Session File Config"
        verbose_name_plural = "Session File Configs"

    def __str__(self):
        return self.name


class SessionFileDefinition(models.Model):
    """Definition of a session file template."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    config_id = models.CharField(max_length=255)
    filename = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    default_content = models.TextField()
    required = models.IntegerField(default=0) # SQLAlchemy used Integer for bool
    sync_on_exit = models.IntegerField(default=1) # SQLAlchemy used Integer for bool
    sort_order = models.IntegerField(default=0)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "session_file_definitions"
        verbose_name = "Session File Definition"
        verbose_name_plural = "Session File Definitions"

    def __str__(self):
        # Look up config name instead of showing UUID
        try:
            config = SessionFileConfig.objects.get(pk=self.config_id)
            return f"{config.name}:{self.filename}"
        except SessionFileConfig.DoesNotExist:
            return f"{self.config_id}:{self.filename}"


class SessionFile(models.Model):
    """Instance of a session file for an agent."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    agent = models.ForeignKey(
        'wintermute.Agent',
        on_delete=models.CASCADE,
        db_column='agent_id',
        related_name='session_files',
    )
    definition = models.ForeignKey(
        SessionFileDefinition,
        on_delete=models.CASCADE,
        db_column='definition_id',
        related_name='session_files',
    )
    content = models.TextField()
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "session_files"
        verbose_name = "Session File"
        verbose_name_plural = "Session Files"

    def __str__(self):
        return f"Session file for {self.agent_id}"


class Channel(models.Model):
    """Communication channel configuration for agents."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    agent_id = models.CharField(max_length=255)
    type = models.CharField(max_length=255) # slack, telegram, discord
    name = models.CharField(max_length=255) # e.g. claude/boreas
    external_channel_id = models.CharField(max_length=255, null=True, blank=True)
    enabled = models.IntegerField(default=1) # SQLAlchemy used Integer for bool
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "channels"
        verbose_name = "Channel"
        verbose_name_plural = "Channels"

    def __str__(self):
        return f"{self.type}:{self.name}"


class AgentWake(models.Model):
    """Scheduled wake-up for an agent session."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    agent_session_id = models.CharField(max_length=255)
    created_at = models.CharField(max_length=255) # ISO datetime string
    wake_at = models.CharField(max_length=255) # ISO datetime string
    duration_seconds = models.IntegerField()
    context = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=255) # pending, fired, cancelled
    fired_at = models.CharField(max_length=255, null=True, blank=True)
    cancelled_at = models.CharField(max_length=255, null=True, blank=True)
    cancelled_by = models.CharField(max_length=255, null=True, blank=True) # user, agent, system
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "agent_wakes"
        indexes = [
            models.Index(fields=["agent_session_id"], name="ix_aw_session_id"),
            models.Index(fields=["status", "wake_at"], name="ix_aw_status_wake_at"),
        ]
        verbose_name = "Agent Wake"
        verbose_name_plural = "Agent Wakes"

    def __str__(self):
        return f"Wake for {self.agent_session_id} at {self.wake_at}"
