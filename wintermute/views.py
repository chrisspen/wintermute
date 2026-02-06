"""Views for Wintermute Django admin."""

import logging
import os
import signal
from pathlib import Path

from django.http import JsonResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

logger = logging.getLogger(__name__)

# Project root directory
REPO_ROOT = Path(__file__).resolve().parent.parent


def dashboard_callback(request, context):
    """
    Callback for the Unfold admin dashboard.

    This can be used to add custom data to the dashboard context.
    """
    # Add any custom dashboard data here
    # For now, just return the context as-is
    return context


def _restart_script(script_name: str, pid_file: str) -> dict:
    """Restart a service by sending SIGTERM, then SIGKILL if needed."""
    import time
    logger.info("Restart requested for %s", script_name)
    killed = []
    pid = None

    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r", encoding="utf-8") as handle:
                pid = int(handle.read().strip() or "0")
        except ValueError:
            pid = None

    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
            logger.info("Sent SIGTERM to PID %s for %s", pid, script_name)
        except ProcessLookupError:
            logger.warning("PID %s not found for %s", pid, script_name)
            return {"ok": True, "killed": [], "message": f"PID {pid} not found for {script_name}"}
        except Exception as exc:
            logger.warning("Failed to stop pid %s for %s: %s", pid, script_name, exc)
            return {"ok": False, "killed": [], "message": str(exc)}

        # Wait up to 3 seconds for graceful shutdown
        for _ in range(6):
            time.sleep(0.5)
            try:
                os.kill(pid, 0) # Check if still running
            except ProcessLookupError:
                logger.info("Process %s stopped gracefully", pid)
                return {"ok": True, "killed": killed, "message": f"Restart signal sent to {script_name}"}

        # Still running, send SIGKILL
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("Sent SIGKILL to PID %s for %s", pid, script_name)
        except ProcessLookupError:
            pass # Already dead

    return {
        "ok": True,
        "killed": killed,
        "message": f"Restart signal sent to {script_name}" if killed else f"No running process found for {script_name}",
    }


@api_view(['POST'])
@permission_classes([IsAdminUser])
def restart_web(request):
    """Restart the web server by sending SIGTERM to the PID file."""
    pid_file = os.environ.get("WINTERMUTE_WEB_PID_FILE", str(REPO_ROOT / ".runtime" / "web.pid"))
    result = _restart_script("run_web.sh", pid_file)
    return JsonResponse(result)


@api_view(['POST'])
@permission_classes([IsAdminUser])
def restart_supervisor(request):
    """Restart the supervisor by sending SIGTERM to the PID file."""
    pid_file = os.environ.get("WINTERMUTE_SUPERVISOR_PID_FILE", str(REPO_ROOT / ".runtime" / "supervisor.pid"))
    result = _restart_script("run_supervisor.sh", pid_file)
    return JsonResponse(result)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def service_status(request):
    """Get the status of web and supervisor services."""

    def get_service_info(pid_file: str, started_file: str) -> dict:
        pid = None
        started_at = None
        running = False

        if os.path.exists(pid_file):
            try:
                with open(pid_file, "r", encoding="utf-8") as f:
                    pid = int(f.read().strip() or "0")
                # Check if process is actually running
                if pid:
                    try:
                        os.kill(pid, 0) # Signal 0 just checks if process exists
                        running = True
                    except ProcessLookupError:
                        running = False
            except (ValueError, IOError):
                pass

        if os.path.exists(started_file):
            try:
                with open(started_file, "r", encoding="utf-8") as f:
                    started_at = f.read().strip()
            except IOError:
                pass

        return {
            "pid": pid,
            "running": running,
            "started_at": started_at,
        }

    runtime_dir = REPO_ROOT / ".runtime"

    return JsonResponse({
        "web":
        get_service_info(
            os.environ.get("WINTERMUTE_WEB_PID_FILE", str(runtime_dir / "web.pid")),
            os.environ.get("WINTERMUTE_WEB_STARTED_FILE", str(runtime_dir / "web.started")),
        ),
        "supervisor":
        get_service_info(
            os.environ.get("WINTERMUTE_SUPERVISOR_PID_FILE", str(runtime_dir / "supervisor.pid")),
            os.environ.get("WINTERMUTE_SUPERVISOR_STARTED_FILE", str(runtime_dir / "supervisor.started")),
        ),
    })
