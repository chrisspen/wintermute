"""Django app configuration for Wintermute."""

from django.apps import AppConfig


class WintermuteConfig(AppConfig):
    """Configuration for the Wintermute Django app."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "wintermute"
    verbose_name = "Wintermute"

    def ready(self):
        """Import signal handlers and perform app initialization."""
        # Import signals if needed
        # import wintermute.signals  # noqa
        pass
