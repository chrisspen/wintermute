"""Django settings for Wintermute."""

import os
from pathlib import Path

from django.templatetags.static import static
from django.urls import reverse

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

#SECRET_KEY = 'django-insecure-^299krhxt2adgqdlwrj-*-vm(a@yhuwa&e_z9h!_y2i-1dub%x'
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "django-insecure-^299krhxt2adgqdlwrj-*-vm(a@yhuwa&e_z9h!_y2i-1dub%x")

DEBUG = True

ALLOWED_HOSTS = ["*"]

# Application definition

INSTALLED_APPS = [
    # Daphne must be first for ASGI
    "daphne",

    # Unfold admin must come before django.contrib.admin
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "unfold.contrib.import_export",
    "unfold.contrib.guardian",
    "unfold.contrib.simple_history",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # Third-party
    "channels",
    "rest_framework",
    "rest_framework.authtoken",
    "django_admin_flexlist",

    # Wintermute
    "wintermute",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware", # Serve static files
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Channels configuration - use in-memory layer for development
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

# Database
# Point to existing Wintermute database
DB_PATH = os.path.expanduser(os.environ.get("WINTERMUTE_DB", "~/dbs/wintermute/wintermute.db"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": DB_PATH,
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# Internationalization
LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}

# Default primary key field type
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom user model
AUTH_USER_MODEL = "wintermute.User"

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'wintermute.auth_backend.WintermuteAuthBackend',
]

# Redirect to admin after login
LOGIN_REDIRECT_URL = '/admin/'

# Django REST Framework configuration
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
}

# Unfold admin configuration
UNFOLD = {
    "SITE_TITLE": "Wintermute 🤖",
    "SITE_HEADER": "Wintermute Admin",
    "SITE_URL": "/",
    "SITE_ICON": None,
    "SITE_LOGO": None,
    "SITE_SYMBOL": "radio_button_checked",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "ENVIRONMENT": "wintermute.util.get_environment",
    "DASHBOARD_CALLBACK": "wintermute.views.dashboard_callback",
    "LOGIN": {
        "image": lambda request: None,
    },
    "COLORS": {
        "primary": {
            "50": "250 245 255",
            "100": "243 232 255",
            "200": "233 213 255",
            "300": "216 180 254",
            "400": "192 132 252",
            "500": "168 85 247",
            "600": "147 51 234",
            "700": "126 34 206",
            "800": "107 33 168",
            "900": "88 28 135",
        },
    },
    "EXTENSIONS": {
        "modeltranslation": {
            "flags": {
                "en": "🇬🇧",
                "fr": "🇫🇷",
                "nl": "🇳🇱",
            },
        },
    },
    "SIDEBAR": {
        "show_search":
        True,
        "show_all_applications":
        True,
        "navigation": [
            {
                "title":
                "Supervisor",
                "separator":
                True,
                "items": [
                    {
                        "title": "Task Sources",
                        "icon": "source",
                        "link": lambda request: reverse("admin:wintermute_tasksource_changelist")
                    },
                    {
                        "title": "Work Items",
                        "icon": "work",
                        "link": lambda request: reverse("admin:wintermute_workitem_changelist")
                    },
                    {
                        "title": "Work Item Runs",
                        "icon": "history",
                        "link": lambda request: reverse("admin:wintermute_workitemrun_changelist")
                    },
                ],
            },
            {
                "title":
                "Projects & Agents",
                "separator":
                True,
                "items": [
                    {
                        "title": "Projects",
                        "icon": "folder",
                        "link": lambda request: reverse("admin:wintermute_project_changelist")
                    },
                    {
                        "title": "Tickets",
                        "icon": "task",
                        "link": lambda request: reverse("admin:wintermute_ticket_changelist")
                    },
                    {
                        "title": "Agents",
                        "icon": "smart_toy",
                        "link": lambda request: reverse("admin:wintermute_agent_changelist")
                    },
                    {
                        "title": "Agent Sessions",
                        "icon": "terminal",
                        "link": lambda request: reverse("admin:wintermute_agentsession_changelist")
                    },
                    {
                        "title": "VM Targets",
                        "icon": "computer",
                        "link": lambda request: reverse("admin:wintermute_vmtarget_changelist")
                    },
                    {
                        "title": "Session File Configs",
                        "icon": "settings",
                        "link": lambda request: reverse("admin:wintermute_sessionfileconfig_changelist")
                    },
                    {
                        "title": "Session File Definitions",
                        "icon": "description",
                        "link": lambda request: reverse("admin:wintermute_sessionfiledefinition_changelist")
                    },
                    {
                        "title": "Session Files",
                        "icon": "draft",
                        "link": lambda request: reverse("admin:wintermute_sessionfile_changelist")
                    },
                ],
            },
            {
                "title":
                "Issue Sources",
                "separator":
                True,
                "items": [
                    {
                        "title": "Issue Sources",
                        "icon": "bug_report",
                        "link": lambda request: reverse("admin:wintermute_issuesource_changelist")
                    },
                    {
                        "title": "Remote Tokens",
                        "icon": "vpn_key",
                        "link": lambda request: reverse("admin:wintermute_remotetoken_changelist")
                    },
                ],
            },
            {
                "title":
                "Administration",
                "separator":
                True,
                "items": [
                    {
                        "title": "Users",
                        "icon": "person",
                        "link": lambda request: reverse("admin:wintermute_user_changelist")
                    },
                    {
                        "title": "API Tokens",
                        "icon": "key",
                        "link": lambda request: reverse("admin:authtoken_token_changelist")
                    },
                    {
                        "title": "Channels",
                        "icon": "chat",
                        "link": lambda request: reverse("admin:wintermute_channel_changelist")
                    },
                ],
            },
        ],
    },
}
