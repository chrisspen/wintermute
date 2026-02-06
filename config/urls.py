"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.views.generic import RedirectView

from wintermute import agent_views, views

urlpatterns = [
    path('', RedirectView.as_view(url='/admin/', permanent=False)),
    path('admin/', admin.site.urls),
    path('', include('django_admin_flexlist.urls')),
    # Agent terminal views (agent_id is a UUID string)
    path('agents/<str:agent_id>/session/status/', agent_views.agent_session_status, name='agent_session_status'),
    path('agents/<str:agent_id>/session/start/', agent_views.agent_start_session, name='agent_start_session'),
    path('agents/<str:agent_id>/session/stop/', agent_views.agent_stop_session, name='agent_stop_session'),
    path('terminal/<str:session_id>/', agent_views.terminal_view, name='terminal_view'),
    # Session API endpoints
    path('api/sessions/<str:session_id>/message', agent_views.session_send_message, name='session_send_message'),
    path('api/sessions/<str:session_id>/output', agent_views.session_output, name='session_output'),
    # Admin API endpoints (compatible with old FastAPI paths)
    path('api/admin/restart-web', views.restart_web, name='api_restart_web'),
    path('api/admin/restart-supervisor', views.restart_supervisor, name='api_restart_supervisor'),
    path('api/admin/status', views.service_status, name='api_service_status'),
]
