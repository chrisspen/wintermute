"""Project and ticket-related models for Wintermute."""

import uuid
from django.db import models


def generate_uuid():
    return str(uuid.uuid4())


class Project(models.Model):
    """Top-level workspace with Slack channel and linked VM targets."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    name = models.CharField(max_length=255)
    slug = models.CharField(max_length=255, unique=True)
    symbol = models.CharField(max_length=255, unique=True, null=True, blank=True)
    slack_channel_id = models.CharField(max_length=255, null=True, blank=True)
    prompt_template = models.TextField(null=True, blank=True)
    max_repo_resources = models.IntegerField(default=3)
    repo_mode = models.CharField(max_length=255, null=True, blank=True)
    repo_path = models.CharField(max_length=255, null=True, blank=True)
    repo_url = models.CharField(max_length=255, null=True, blank=True)
    master_branch_name = models.CharField(max_length=255, default="master")
    build_status_image_url = models.CharField(max_length=255, null=True, blank=True)
    # Issue source fields (merged from IssueSource)
    provider = models.CharField(max_length=255, null=True, blank=True)
    source_token_id = models.CharField(max_length=255, null=True, blank=True)
    source_agent_id = models.CharField(max_length=255, null=True, blank=True)
    source_repo = models.CharField(max_length=255, null=True, blank=True)
    issue_state = models.CharField(max_length=255, null=True, blank=True)
    issue_labels_json = models.TextField(default="[]")
    source_enabled = models.IntegerField(default=0)
    auto_start = models.IntegerField(default=0)
    poll_interval_seconds = models.IntegerField(default=300)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "projects"
        verbose_name = "Project"
        verbose_name_plural = "Projects"

    def __str__(self):
        return self.name


class Sprint(models.Model):
    """Time-boxed iteration for organizing work items."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    name = models.CharField(max_length=255)
    start_date = models.CharField(max_length=255) # ISO datetime string
    end_date = models.CharField(max_length=255) # ISO datetime string
    enabled = models.IntegerField(default=1)
    status = models.CharField(max_length=255, default="active")
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "sprints"
        verbose_name = "Sprint"
        verbose_name_plural = "Sprints"

    def __str__(self):
        return self.name


class TicketSprint(models.Model):
    """Many-to-many relationship between tickets and sprints."""

    ticket_id = models.CharField(max_length=255, primary_key=True)
    sprint_id = models.CharField(max_length=255)
    created_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "ticket_sprints"
        unique_together = [["ticket_id", "sprint_id"]]
        verbose_name = "Ticket Sprint"
        verbose_name_plural = "Ticket Sprints"

    def __str__(self):
        return f"Ticket {self.ticket_id} in Sprint {self.sprint_id}"


class Ticket(models.Model):
    """Lightweight work item tracking within a project."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, db_column='project_id')
    agent_id = models.CharField(max_length=255, null=True, blank=True)
    vm_target_id = models.CharField(max_length=255, null=True, blank=True)
    sprint_id = models.CharField(max_length=255, null=True, blank=True)
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    internal_notes = models.TextField(null=True, blank=True)
    assigned_to = models.CharField(max_length=255, null=True, blank=True)
    estimate = models.CharField(max_length=255, null=True, blank=True)
    hours = models.CharField(max_length=255, null=True, blank=True)
    story_points = models.CharField(max_length=255, null=True, blank=True)
    priority = models.CharField(max_length=255, null=True, blank=True)
    status = models.CharField(max_length=255)
    source_url = models.TextField(null=True, blank=True)
    github_comments_json = models.TextField(null=True, blank=True)
    github_comments_fetched_at = models.CharField(max_length=255, null=True, blank=True)
    auto_start = models.IntegerField(default=0)
    count = models.IntegerField(null=True, blank=True)
    created_by_id = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "tickets"
        constraints = [models.UniqueConstraint(fields=["project_id", "count"], name="uq_tickets_project_count")]
        verbose_name = "Ticket"
        verbose_name_plural = "Tickets"

    @property
    def name(self):
        """Return ticket name in PROJECT-COUNT format."""
        return f"{self.project.symbol}-{self.count}"

    def __str__(self):
        return f"{self.project.symbol}-{self.count}: {self.title}"


class TicketHistory(models.Model):
    """Audit log of ticket field changes."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    ticket_id = models.CharField(max_length=255, db_index=True)
    user_id = models.CharField(max_length=255, null=True, blank=True)
    field_name = models.CharField(max_length=255)
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "ticket_history"
        verbose_name = "Ticket History"
        verbose_name_plural = "Ticket Histories"

    def __str__(self):
        return f"Ticket {self.ticket_id}: {self.field_name} changed"


class Comment(models.Model):
    """User comment on a ticket or work item."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    ticket_id = models.CharField(max_length=255, null=True, blank=True)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    project_id = models.CharField(max_length=255, null=True, blank=True)
    agent_id = models.CharField(max_length=255, null=True, blank=True)
    agent_session_id = models.CharField(max_length=255, null=True, blank=True)
    author = models.CharField(max_length=255, null=True, blank=True)
    source_id = models.CharField(max_length=255, null=True, blank=True)
    issue_number = models.IntegerField(null=True, blank=True)
    body = models.TextField()
    public = models.IntegerField() # SQLAlchemy used Integer for bool
    approved = models.IntegerField() # SQLAlchemy used Integer for bool
    sent = models.IntegerField() # SQLAlchemy used Integer for bool
    sent_at = models.CharField(max_length=255, null=True, blank=True)
    origin = models.CharField(max_length=255, null=True, blank=True)
    seconds_spent_working = models.IntegerField(null=True, blank=True)
    created_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "comments"
        verbose_name = "Comment"
        verbose_name_plural = "Comments"

    def __str__(self):
        if self.ticket_id:
            return f"Comment on Ticket {self.ticket_id}"
        return f"Comment {self.id}"


class RepoResource(models.Model):
    """Reusable repo checkout path for clone mode sessions."""

    id = models.CharField(max_length=255, primary_key=True, default=generate_uuid, editable=False)
    project_id = models.CharField(max_length=255)
    agent_id = models.CharField(max_length=255, null=True, blank=True)
    repo_mode = models.CharField(max_length=255)
    path = models.TextField()
    status = models.CharField(max_length=255)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    last_used_at = models.CharField(max_length=255, null=True, blank=True) # ISO datetime string
    created_at = models.CharField(max_length=255) # ISO datetime string
    updated_at = models.CharField(max_length=255) # ISO datetime string

    class Meta:
        db_table = "repo_resources"
        verbose_name = "Repo Resource"
        verbose_name_plural = "Repo Resources"

    def __str__(self):
        return f"{self.project_id} ({self.repo_mode}): {self.path}"
