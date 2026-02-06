"""Django admin configuration for Wintermute."""

from unfold.admin import ModelAdmin, TabularInline
from django_admin_flexlist.admin import FlexListAdmin
from django.contrib import admin
from rest_framework.authtoken.models import Token, TokenProxy


# Combine Unfold and FlexList functionality
class BaseAdmin(FlexListAdmin, ModelAdmin):
    """Base admin class combining Unfold theme with FlexList column management."""

    def get_readonly_fields(self, request, obj=None):
        """Make created_at and updated_at readonly on all models."""
        readonly = list(super().get_readonly_fields(request, obj))
        for field_name in ['created_at', 'updated_at']:
            if self._has_field(field_name) and field_name not in readonly:
                readonly.append(field_name)
        return readonly

    def _has_field(self, field_name):
        """Check if the model has a field with the given name."""
        try:
            self.model._meta.get_field(field_name)
            return True
        except Exception:
            return False


from .models import (
    # Supervisor
    TaskSource,
    WorkItem,
    WorkItemRun,
    SupervisorState,
    # Auth
    User,
    ColumnPreference,
    Credential,
    # Projects
    Project,
    Sprint,
    TicketSprint,
    Ticket,
    TicketHistory,
    Comment,
    RepoResource,
    # Infrastructure
    VMTarget,
    # Sources
    IssueSource,
    RemoteToken,
    # Agents
    Agent,
    MetricDefinition,
    AgentMetricsLog,
    AgentSession,
    AgentResponse,
    # Sessions
    SessionFileConfig,
    SessionFileDefinition,
    SessionFile,
    Channel,
    AgentWake,
)


@admin.register(TaskSource)
class TaskSourceAdmin(BaseAdmin):
    list_display = ["id", "enabled", "base_priority", "poll_interval_seconds", "created_at"]
    list_filter = ["enabled"]
    search_fields = ["id"]


@admin.register(WorkItem)
class WorkItemAdmin(BaseAdmin):
    list_display = ["work_id", "source_id", "status", "priority", "attempts", "created_at"]
    list_filter = ["status", "source_id"]
    search_fields = ["work_id"]


@admin.register(WorkItemRun)
class WorkItemRunAdmin(BaseAdmin):
    list_display = ["run_id", "work_id", "status", "started_at", "ended_at"]
    list_filter = ["status"]
    search_fields = ["work_id"]


@admin.register(SupervisorState)
class SupervisorStateAdmin(BaseAdmin):
    list_display = ["id", "status", "current_work_id", "queue_depth", "updated_at"]


@admin.register(User)
class UserAdmin(BaseAdmin):
    list_display = ["id", "username", "is_staff", "is_superuser", "created_at"]
    search_fields = ["username"]
    list_filter = ["is_staff", "is_superuser"]


@admin.register(ColumnPreference)
class ColumnPreferenceAdmin(BaseAdmin):
    list_display = ["id", "user_id", "model", "created_at"]
    list_filter = ["model"]


# Unregister DRF's default Token and TokenProxy admins
for token_model in [Token, TokenProxy]:
    try:
        admin.site.unregister(token_model)
    except admin.sites.NotRegistered:
        pass


# Re-register Token with Unfold styling (use Token, not TokenProxy)
@admin.register(Token)
class TokenAdmin(BaseAdmin):
    list_display = ["key", "user", "created"]
    search_fields = ["user__username"]
    raw_id_fields = ["user"]
    readonly_fields = ["key", "created"]
    fields = ["user", "key"] # key shown but read-only; auto-generated on save

    def add_view(self, request, form_url="", extra_context=None):
        # Hide key field on add form since it's auto-generated
        self.readonly_fields = ["created"]
        self.fields = ["user"]
        return super().add_view(request, form_url, extra_context)

    def change_view(self, request, object_id, form_url="", extra_context=None):
        # Show key as read-only on change form
        self.readonly_fields = ["key", "created"]
        self.fields = ["user", "key"]
        return super().change_view(request, object_id, form_url, extra_context)


# Override Token.__str__ to show username instead of key
Token.__str__ = lambda self: f"Token for {self.user.username}" if self.user else f"Token {self.key[:8]}..."


@admin.register(Credential)
class CredentialAdmin(BaseAdmin):
    list_display = ["id", "name", "provider", "created_at"]
    list_filter = ["provider"]
    search_fields = ["name"]


@admin.register(Project)
class ProjectAdmin(BaseAdmin):
    list_display = ["slug", "name", "provider_icon", "symbol", "repo_mode", "actions_column", "created_at"]
    list_filter = ["repo_mode", "provider"]
    search_fields = ["name", "slug"]

    def actions_column(self, obj):
        """Display action buttons for the project."""
        from django.utils.html import mark_safe
        from django.urls import reverse

        create_ticket_url = reverse("admin:wintermute_ticket_add") + f"?project_id={obj.id}"
        return mark_safe(
            f'<a href="{create_ticket_url}" class="button" style="padding:4px 8px;background:#5b21b6;color:white;border-radius:4px;text-decoration:none;font-size:12px;">+ Ticket</a>'
        )

    actions_column.short_description = "Actions"

    def provider_icon(self, obj):
        """Display provider icon based on the provider field."""
        from django.utils.html import mark_safe

        if not obj.provider:
            return ""

        provider_icons = {
            "github":
            '<svg style="width:20px;height:20px;vertical-align:middle" viewBox="0 0 16 16"><path fill="currentColor" d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>',
            "gitlab":
            '<svg style="width:20px;height:20px;vertical-align:middle" viewBox="0 0 16 16"><path fill="#FC6D26" d="M8 15.1l2.8-8.6H5.2z"/><path fill="#E24329" d="M8 15.1l-2.8-8.6H1.6z"/><path fill="#FC6D26" d="M1.6 6.5L.2 10.7c-.1.4 0 .8.3 1l7.5 5.4z"/><path fill="#FCA326" d="M1.6 6.5h3.6L3.7 1c-.2-.5-.8-.5-1 0z"/><path fill="#E24329" d="M8 15.1l2.8-8.6h3.6z"/><path fill="#FC6D26" d="M14.4 6.5l1.4 4.2c.1.4 0 .8-.3 1l-7.5 5.4z"/><path fill="#FCA326" d="M14.4 6.5h-3.6L12.3 1c.2-.5.8-.5 1 0z"/></svg>',
        }

        icon = provider_icons.get(obj.provider.lower(), "")
        if not icon:
            return ""

        # Build URL - use repo_url if available, otherwise construct from provider and source_repo
        url = obj.repo_url
        if not url and obj.source_repo:
            if obj.provider.lower() == "github":
                url = f"https://github.com/{obj.source_repo}"
            elif obj.provider.lower() == "gitlab":
                url = f"https://gitlab.com/{obj.source_repo}"

        if url:
            return mark_safe(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{icon}</a>')
        return mark_safe(icon)

    provider_icon.short_description = "Provider"


@admin.register(Sprint)
class SprintAdmin(BaseAdmin):
    list_display = ["name", "start_date", "end_date", "status", "enabled"]
    list_filter = ["status", "enabled"]
    search_fields = ["name"]


@admin.register(TicketSprint)
class TicketSprintAdmin(BaseAdmin):
    list_display = ["ticket_id", "sprint_id", "created_at"]


@admin.register(Ticket)
class TicketAdmin(BaseAdmin):
    list_display = ["ticket_code", "title", "project", "status", "assigned_to", "created_at"]
    list_filter = ["status", "project"]
    search_fields = ["title", "description"]

    def ticket_code(self, obj):
        """Display ticket as PROJECT-COUNT format."""
        return f"{obj.project.symbol}-{obj.count}"

    ticket_code.short_description = "Ticket"
    ticket_code.admin_order_field = "count"


@admin.register(TicketHistory)
class TicketHistoryAdmin(BaseAdmin):
    list_display = ["ticket_id", "field_name", "user_id", "created_at"]
    list_filter = ["field_name"]
    search_fields = ["ticket_id"]


@admin.register(Comment)
class CommentAdmin(BaseAdmin):
    list_display = ["id", "ticket_id", "author", "public", "approved", "sent", "created_at"]
    list_filter = ["public", "approved", "sent", "origin"]
    search_fields = ["body"]


@admin.register(RepoResource)
class RepoResourceAdmin(BaseAdmin):
    list_display = ["id", "project_id", "repo_mode", "status", "last_used_at"]
    list_filter = ["repo_mode", "status"]


@admin.register(VMTarget)
class VMTargetAdmin(BaseAdmin):
    list_display = ["name", "host", "user", "port", "required_reserve_memory_gb"]
    search_fields = ["name", "host"]


@admin.register(IssueSource)
class IssueSourceAdmin(BaseAdmin):
    list_display = ["id", "provider", "project_id", "repo", "enabled", "auto_start"]
    list_filter = ["provider", "enabled", "auto_start"]
    search_fields = ["repo"]


@admin.register(RemoteToken)
class RemoteTokenAdmin(BaseAdmin):
    list_display = ["id", "provider", "user_login", "note", "created_at"]
    list_filter = ["provider"]
    search_fields = ["user_login", "note"]


class SessionFileInline(TabularInline):
    """Inline for viewing session files on Agent admin page."""
    model = SessionFile
    extra = 0
    fields = ['definition', 'created_at', 'updated_at', 'edit_link']
    readonly_fields = ['definition', 'created_at', 'updated_at', 'edit_link']
    can_delete = False
    max_num = 0 # Prevent adding new inline rows
    template = "admin/wintermute/edit_inline/tabular_no_scroll.html"

    def edit_link(self, obj):
        """Link to edit the session file in a new tab."""
        if obj.pk:
            from django.utils.html import mark_safe
            from django.urls import reverse
            url = reverse("admin:wintermute_sessionfile_change", args=[obj.pk])
            return mark_safe(f'<a href="{url}" target="_blank">Edit</a>')
        return "-"

    edit_link.short_description = ""


@admin.register(Agent)
class AgentAdmin(BaseAdmin):
    list_display = ["slug", "name", "vm_target", "session_mode", "autostart", "created_at"]
    list_filter = ["session_mode", "autostart", "vm_target"]
    search_fields = ["name", "slug"]
    raw_id_fields = ["vm_target", "api_token"]
    change_form_template = "admin/wintermute/agent/change_form.html"
    inlines = [SessionFileInline]

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["agent_id"] = object_id
        # Pass session_mode to template for conditional UI
        try:
            agent = Agent.objects.get(pk=object_id)
            extra_context["session_mode"] = agent.session_mode
        except Agent.DoesNotExist:
            extra_context["session_mode"] = "tmux"
        return super().change_view(request, object_id, form_url, extra_context)


@admin.register(MetricDefinition)
class MetricDefinitionAdmin(BaseAdmin):
    list_display = ["metric_type", "recording_frequency_minutes", "enabled", "created_at"]
    list_filter = ["enabled"]
    search_fields = ["metric_type"]


@admin.register(AgentMetricsLog)
class AgentMetricsLogAdmin(BaseAdmin):
    list_display = ["agent_id", "metric_definition_id", "value", "recorded_at"]
    list_filter = ["agent_id", "metric_definition_id"]


@admin.register(AgentSession)
class AgentSessionAdmin(BaseAdmin):
    list_display = ["id", "agent_id", "project_id", "status", "created_at"]
    list_filter = ["status", "agent_id"]
    search_fields = ["id"]


@admin.register(AgentResponse)
class AgentResponseAdmin(BaseAdmin):
    list_display = ["id", "agent_id", "pattern", "created_at"]
    list_filter = ["agent_id"]
    search_fields = ["pattern"]


@admin.register(SessionFileConfig)
class SessionFileConfigAdmin(BaseAdmin):
    list_display = ["name", "description", "created_at"]
    search_fields = ["name"]


@admin.register(SessionFileDefinition)
class SessionFileDefinitionAdmin(BaseAdmin):
    list_display = ["filename", "config_id", "required", "sync_on_exit", "sort_order"]
    list_filter = ["config_id", "required", "sync_on_exit"]
    search_fields = ["filename"]


@admin.register(SessionFile)
class SessionFileAdmin(BaseAdmin):
    list_display = ["id", "agent", "definition", "created_at"]
    list_filter = ["agent"]
    raw_id_fields = ["agent", "definition"]


@admin.register(Channel)
class ChannelAdmin(BaseAdmin):
    list_display = ["name", "type", "agent_id", "enabled", "created_at"]
    list_filter = ["type", "enabled"]
    search_fields = ["name"]


@admin.register(AgentWake)
class AgentWakeAdmin(BaseAdmin):
    list_display = ["agent_session_id", "wake_at", "status", "duration_seconds"]
    list_filter = ["status"]
    search_fields = ["agent_session_id"]
