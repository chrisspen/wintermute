"""Issue source and token models for Wintermute."""

import json
import uuid
from django.db import models


def generate_uuid():
    return str(uuid.uuid4())


class IssueSource(models.Model):
    """Configuration for an external issue tracking source."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    provider = models.CharField(max_length=255)
    token_id = models.CharField(max_length=255, null=True, blank=True)
    agent_id = models.CharField(max_length=255, null=True, blank=True)
    project_id = models.CharField(max_length=255)
    owner = models.CharField(max_length=255, default="") # GitHub owner / GitLab group
    repo = models.CharField(max_length=255)
    state = models.CharField(max_length=255)
    labels_json = models.TextField() # JSON list of labels
    enabled = models.IntegerField() # SQLAlchemy used Integer for bool
    auto_start = models.IntegerField() # SQLAlchemy used Integer for bool
    poll_interval_seconds = models.IntegerField()
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "issue_sources"
        verbose_name = "Issue Source"
        verbose_name_plural = "Issue Sources"

    @property
    def labels(self) -> list[str]:
        """Parse labels_json and return as list."""
        if not self.labels_json:
            return []
        try:
            return json.loads(self.labels_json)
        except json.JSONDecodeError:
            return []

    @property
    def project_path(self) -> str:
        """Return GitLab-style project path (owner/repo)."""
        return f"{self.owner}/{self.repo}"

    def __str__(self):
        return f"{self.provider}:{self.owner}/{self.repo}"


class RemoteToken(models.Model):
    """Generic remote service token for API authentication."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    provider = models.CharField(max_length=255)
    note = models.TextField(null=True, blank=True)
    token = models.TextField()
    base_url = models.CharField(max_length=255, null=True, blank=True)
    user_id = models.CharField(max_length=255, null=True, blank=True)
    user_login = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "remote_tokens"
        verbose_name = "Remote Token"
        verbose_name_plural = "Remote Tokens"

    def __str__(self):
        if self.user_login:
            return f"{self.provider} Token for {self.user_login}"
        return f"{self.provider} Token {self.id}"
