"""Django views for agent session management."""

import json
import logging
import os
import shlex
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone

from django.contrib.admin.views.decorators import staff_member_required
from django.http import JsonResponse
from django.shortcuts import render, get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_GET, require_POST
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser

from .models import Agent, AgentSession, VMTarget, SessionFileConfig, SessionFileDefinition, SessionFile, Comment
from .runner import build_ssh_spec, ensure_vm_tools, run_health_check, check_vm_memory_available, start_session, stop_session, is_session_running, send_input

logger = logging.getLogger(__name__)


def utc_now():
    """Return current UTC time as ISO string."""
    return datetime.now(timezone.utc).isoformat()


@api_view(['GET'])
@permission_classes([IsAdminUser])
def agent_session_status(request, agent_id):
    """Get the current session status for an agent."""
    agent = get_object_or_404(Agent, pk=agent_id)

    # Find running standalone session
    sessions = AgentSession.objects.filter(agent_id=agent_id, status__in=['running', 'blocked']).exclude(ticket_id__isnull=False)

    if not sessions.exists():
        return JsonResponse({
            "running": False,
            "session_id": None,
            "location": None,
            "pid": None,
            "log_path": None,
        })

    session = sessions.first()

    # Check if tmux session is actually running on VM (only for tmux mode)
    if agent.vm_target and agent.session_mode == "tmux":
        from .runner import build_ssh_spec, is_session_running
        spec = build_ssh_spec(agent.vm_target, agent.required_ssh_options)
        actually_running = is_session_running(spec, session.id)
        if not actually_running and session.status == 'running':
            # Update session status
            session.status = 'done'
            session.updated_at = utc_now()
            session.save()
            return JsonResponse({
                "running": False,
                "session_id": None,
                "location": None,
                "pid": None,
                "log_path": None,
            })

    return JsonResponse({
        "running": True,
        "session_id": session.id,
        "location": session.workspace_path or session.repo_path,
        "pid": None, # TODO: get actual PID
        "log_path": f"/tmp/wintermute-{session.id}.log",
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def agent_start_session(request, agent_id):
    """Start a new session for an agent."""
    agent = get_object_or_404(Agent, pk=agent_id)

    if not agent.vm_target:
        return JsonResponse({"detail": "Agent has no VM target configured"}, status=400)

    vm = agent.vm_target

    # Check if a standalone session already exists
    existing = AgentSession.objects.filter(agent_id=agent_id, status__in=['running', 'blocked']).exclude(ticket_id__isnull=False).first()

    if existing:
        return JsonResponse({"detail": "Session already running"}, status=409)

    # Build SSH spec
    from .runner import build_ssh_spec, ensure_vm_tools, start_session, send_input

    spec = build_ssh_spec(vm, agent.required_ssh_options)

    # Check that required tools are available
    tools_ok, tools_error = ensure_vm_tools(spec, agent.command, agent.session_mode)
    if not tools_ok:
        return JsonResponse({"detail": tools_error}, status=400)

    # Run health check if configured
    if agent.health_command:
        from .runner import run_health_check
        health_ok, health_error = run_health_check(spec, agent.health_command)
        if not health_ok:
            return JsonResponse({"detail": health_error}, status=400)

    # Determine workspace
    if agent.working_directory:
        # Verify the working directory exists on the VM
        check_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"test -d {shlex.quote(agent.working_directory)} && echo exists"]
        result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
        if result.stdout.strip() != "exists":
            return JsonResponse({"detail": f"Working directory does not exist on VM: {agent.working_directory}"}, status=400)
        workspace = agent.working_directory
    else:
        # Create temp workspace on VM
        mktemp_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"mktemp -d /tmp/agent_{agent.slug}_XXXXXXXX"]
        result = subprocess.run(mktemp_cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return JsonResponse({"detail": f"Failed to create workspace: {result.stderr}"}, status=500)
        workspace = result.stdout.strip()

    # Determine session files directory
    if agent.session_directory:
        if agent.session_directory.startswith("/"):
            session_files_dir = agent.session_directory
        else:
            session_files_dir = f"{workspace}/{agent.session_directory}"
        # Ensure session directory exists
        mkdir_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"mkdir -p {shlex.quote(session_files_dir)}"]
        subprocess.run(mkdir_cmd, capture_output=True, text=True, timeout=30)
    else:
        session_files_dir = workspace

    # Create the session record
    session_id = str(uuid.uuid4())
    default_initial_prompt = "Read your AGENTS.md file and then wait for further instructions."
    initial_prompt = agent.initial_prompt or default_initial_prompt

    # Create session
    session = AgentSession.objects.create(
        id=session_id,
        project_id=None,
        agent_id=agent_id,
        ticket_id=None,
        status='running',
        repo_path=workspace,
        thread_ts=None,
        mcp_conversation_id=None,
        initial_prompt=initial_prompt,
        workspace_path=workspace,
        last_output_offset=0,
        awaiting_response=1 if agent.session_mode != "tmux" else 0,
        queued_user_messages=json.dumps([initial_prompt]) if agent.session_mode != "tmux" else None,
        last_user_message=initial_prompt if agent.session_mode != "tmux" else None,
        prompt_sent_at=utc_now() if agent.session_mode != "tmux" else None,
        created_at=utc_now(),
        updated_at=utc_now(),
    )

    # Copy session files to VM
    if agent.session_file_config_id:
        try:
            definitions = SessionFileDefinition.objects.filter(config_id=agent.session_file_config_id)
            session_files = SessionFile.objects.filter(agent_id=agent_id)
            file_map = {sf.definition_id: sf for sf in session_files}

            with tempfile.TemporaryDirectory() as local_tmp:
                for defn in definitions:
                    content = file_map.get(defn.id)
                    if content:
                        file_content = content.content
                    else:
                        file_content = defn.default_content or ""
                    local_path = os.path.join(local_tmp, defn.filename)
                    with open(local_path, "w") as f:
                        f.write(file_content)

                # Write SKILLS.md from template
                skills_template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "wintermute", "skills.md.template")
                if os.path.exists(skills_template_path):
                    with open(skills_template_path, "r") as f:
                        skills_content = f.read()
                    with open(os.path.join(local_tmp, "SKILLS.md"), "w") as f:
                        f.write(skills_content)

                # SCP files to VM
                scp_cmd = ["scp", "-P", str(spec.port), *spec.options, "-r", f"{local_tmp}/.", f"{spec.user}@{spec.host}:{session_files_dir}/"]
                scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
                if scp_result.returncode != 0:
                    logger.warning("Failed to copy session files: %s", scp_result.stderr)
        except Exception as e:
            logger.warning("Error copying session files: %s", e)

    # Start the tmux session (for tmux mode only)
    if agent.session_mode == "tmux":
        # Create AgentRecord-like object for start_session
        from .db import AgentRecord
        agent_record = AgentRecord(
            id=agent.id,
            name=agent.name,
            slug=agent.slug,
            command=agent.command,
            session_mode=agent.session_mode,
            vm_target_id=agent.vm_target_id if agent.vm_target else None,
            required_ssh_options=agent.required_ssh_options,
            env_vars=agent.env_vars,
            mcp_config=agent.mcp_config,
            trust_level=agent.trust_level,
            input_echo_prefix=agent.input_echo_prefix,
            response_prefix=agent.response_prefix,
            llm_base_url=agent.llm_base_url,
            llm_api_key=agent.llm_api_key,
            llm_model=agent.llm_model,
            session_file_config_id=agent.session_file_config_id,
            average_memory_usage_mb=agent.average_memory_usage_mb,
            initial_prompt=agent.initial_prompt,
            working_directory=agent.working_directory,
            session_directory=agent.session_directory,
            autostart=1 if agent.autostart else 0,
            health_command=agent.health_command,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )
        start_session(spec, session_id, agent_record, workspace)

        # Send initial prompt
        if initial_prompt:
            try:
                from .db import AgentSessionRecord
                session_record = AgentSessionRecord(
                    id=session.id,
                    project_id=session.project_id,
                    agent_id=session.agent_id,
                    ticket_id=session.ticket_id,
                    status=session.status,
                    repo_path=session.repo_path,
                    thread_ts=session.thread_ts,
                    mcp_conversation_id=session.mcp_conversation_id,
                    claude_session_id=session.claude_session_id,
                    last_output=session.last_output,
                    last_output_offset=session.last_output_offset,
                    output_buffer=session.output_buffer,
                    output_buffer_updated_at=session.output_buffer_updated_at,
                    prompt_pending=session.prompt_pending,
                    prompt_sent_at=session.prompt_sent_at,
                    last_output_at=session.last_output_at,
                    awaiting_response=session.awaiting_response,
                    last_user_message=session.last_user_message,
                    queued_user_messages=session.queued_user_messages,
                    awaiting_response_offset=session.awaiting_response_offset,
                    initial_prompt=session.initial_prompt,
                    workspace_path=session.workspace_path,
                    created_at=session.created_at,
                    updated_at=session.updated_at,
                )
                send_input(spec, session_record, initial_prompt)
            except Exception as e:
                logger.warning("Failed to send initial prompt: %s", e)

    # Record initial prompt as comment
    if initial_prompt and request.user:
        Comment.objects.create(
            id=str(uuid.uuid4()),
            ticket_id=None,
            session_id=session_id,
            project_id=None,
            agent_id=agent_id,
            author=request.user.username,
            source_id=None,
            issue_number=None,
            body=initial_prompt,
            public=0,
            approved=0,
            sent=0,
            agent_session_id=session_id,
            origin="initial_prompt",
            created_at=utc_now(),
        )

    return JsonResponse({
        "session_id": session_id,
        "location": workspace,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def agent_stop_session(request, agent_id):
    """Stop the current session for an agent."""
    agent = get_object_or_404(Agent, pk=agent_id)

    if not agent.vm_target:
        return JsonResponse({"detail": "Agent has no VM target configured"}, status=400)

    # Find running session
    session = AgentSession.objects.filter(agent_id=agent_id, status__in=['running', 'blocked']).exclude(ticket_id__isnull=False).first()

    if not session:
        return JsonResponse({"detail": "No running session found"}, status=404)

    # Stop the tmux session
    from .runner import build_ssh_spec, stop_session
    spec = build_ssh_spec(agent.vm_target, agent.required_ssh_options)
    stop_session(spec, session.id)

    # Update session status
    session.status = 'done'
    session.updated_at = utc_now()
    session.save()

    return JsonResponse({"status": "stopped"})


@staff_member_required
@require_GET
def terminal_view(request, session_id):
    """Render the terminal page for a session."""
    session = get_object_or_404(AgentSession, pk=session_id)
    agent = get_object_or_404(Agent, pk=session.agent_id)

    return render(request, 'wintermute/terminal.html', {
        'session': session,
        'agent': agent,
        'session_id': session_id,
    })


@api_view(['POST'])
@permission_classes([IsAdminUser])
def session_send_message(request, session_id):
    """Queue a message to send to an agent session."""
    session = get_object_or_404(AgentSession, pk=session_id)

    if session.status not in ['running', 'blocked']:
        return JsonResponse({"detail": "Session is not running"}, status=400)

    # Get message from request body
    message = request.data.get('message', '').strip()
    if not message:
        return JsonResponse({"detail": "Message is required"}, status=400)

    # Get current queue
    raw_queue = session.queued_user_messages or "[]"
    try:
        queue = json.loads(raw_queue)
        if not isinstance(queue, list):
            queue = []
    except json.JSONDecodeError:
        queue = []

    # Add message to queue
    queue.append(message)
    session.queued_user_messages = json.dumps(queue)
    session.updated_at = utc_now()
    session.save()

    # Create comment record for the message
    Comment.objects.create(
        id=str(uuid.uuid4()),
        ticket_id=session.ticket_id,
        session_id=session_id,
        project_id=session.project_id,
        agent_id=session.agent_id,
        author=request.user.username,
        source_id=None,
        issue_number=None,
        body=message,
        public=0,
        approved=0,
        sent=0,
        agent_session_id=session_id,
        origin="api",
        created_at=utc_now(),
    )

    return JsonResponse({
        "status": "queued",
        "queue_length": len(queue),
    })


@api_view(['GET'])
@permission_classes([IsAdminUser])
def session_output(request, session_id):
    """Get recent output from an agent session."""
    session = get_object_or_404(AgentSession, pk=session_id)

    # Get comments for this session (most recent first)
    limit = int(request.query_params.get('limit', 20))
    comments = Comment.objects.filter(agent_session_id=session_id).order_by('-created_at')[:limit]

    return JsonResponse({
        "session_id":
        session_id,
        "status":
        session.status,
        "awaiting_response":
        session.awaiting_response,
        "last_user_message":
        session.last_user_message,
        "last_output":
        session.last_output,
        "output_buffer":
        session.output_buffer,
        "comments": [{
            "id": c.id,
            "author": c.author,
            "body": c.body,
            "origin": c.origin,
            "created_at": c.created_at,
        } for c in reversed(list(comments))],
    })
