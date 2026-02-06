"""Supervisor-related models for Wintermute."""

import json
import uuid
from django.db import models


def generate_uuid():
    return str(uuid.uuid4())


class TaskSource(models.Model):
    """Configuration for a task source that polls for work items."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    enabled = models.IntegerField() # SQLAlchemy used Integer for bool
    base_priority = models.IntegerField()
    poll_interval_seconds = models.IntegerField()
    config_json = models.TextField() # JSON config stored as text
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "task_sources"
        verbose_name = "Task Source"
        verbose_name_plural = "Task Sources"

    @property
    def config(self) -> dict:
        """Parse config_json and return as dict."""
        if not self.config_json:
            return {}
        try:
            return json.loads(self.config_json)
        except json.JSONDecodeError:
            return {}

    @config.setter
    def config(self, value: dict) -> None:
        """Serialize dict to config_json."""
        self.config_json = json.dumps(value) if value else "{}"

    def __str__(self):
        return f"TaskSource({self.id})"


class WorkItem(models.Model):
    """A unit of work managed by the supervisor."""

    work_id = models.CharField(max_length=255, primary_key=True)
    source_id = models.CharField(max_length=255)
    priority = models.IntegerField()
    status = models.CharField(max_length=255)
    checkpoint_json = models.TextField() # JSON checkpoint stored as text
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string
    run_after = models.CharField(max_length=255) # ISO datetime string
    attempts = models.IntegerField()
    last_error = models.CharField(max_length=255, null=True, blank=True)
    last_traceback = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "work_items"
        verbose_name = "Work Item"
        verbose_name_plural = "Work Items"

    @property
    def checkpoint(self) -> dict:
        """Parse checkpoint_json and return as dict."""
        if not self.checkpoint_json:
            return {}
        try:
            return json.loads(self.checkpoint_json)
        except json.JSONDecodeError:
            return {}

    @checkpoint.setter
    def checkpoint(self, value: dict) -> None:
        """Serialize dict to checkpoint_json."""
        self.checkpoint_json = json.dumps(value) if value else "{}"

    def __str__(self):
        return f"WorkItem({self.work_id})"


class WorkItemRun(models.Model):
    """Record of a work item execution."""

    run_id = models.AutoField(primary_key=True)
    work_id = models.CharField(max_length=255)
    started_at = models.CharField(max_length=255) # ISO datetime string
    ended_at = models.CharField(max_length=255, null=True, blank=True) # ISO datetime string
    status = models.CharField(max_length=255)
    error = models.TextField(null=True, blank=True)

    class Meta:
        db_table = "work_item_runs"
        verbose_name = "Work Item Run"
        verbose_name_plural = "Work Item Runs"

    def __str__(self):
        return f"WorkItemRun({self.run_id} for {self.work_id})"


class SupervisorState(models.Model):
    """Current state of the supervisor process."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    status = models.CharField(max_length=255)
    current_work_id = models.CharField(max_length=255, null=True, blank=True)
    last_action = models.CharField(max_length=255)
    queue_depth = models.IntegerField()
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "supervisor_state"
        verbose_name = "Supervisor State"
        verbose_name_plural = "Supervisor States"

    def __str__(self):
        return f"SupervisorState({self.status})"
