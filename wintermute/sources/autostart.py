"""Autostart TaskSource - starts agents marked for autostart every 5 minutes."""

from __future__ import annotations

import logging
import shlex
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from wintermute.services.database import AsyncDatabase
from wintermute.utils import utc_now
from wintermute.runner import build_ssh_spec, ensure_vm_tools, check_vm_memory_available, start_session
from wintermute.sources.base import TaskSource, WorkItem, WorkItemContext, WorkItemDraft


@dataclass
class AutostartWorkItem(WorkItem):
    work_id: str
    priority: int
    source_id: str
    checkpoint: dict[str, Any]

    async def resume(self, ctx: WorkItemContext) -> None:
        logger = logging.getLogger(__name__)
        agent_id = self.checkpoint.get("agent_id")
        if not agent_id:
            logger.error("AutostartWorkItem missing agent_id in checkpoint")
            return

        db: AsyncDatabase = ctx.db
        agent = await db.get_agent(agent_id)
        if not agent:
            logger.warning("Autostart: agent %s not found", agent_id)
            return

        if not agent.autostart:
            logger.info("Autostart: agent %s no longer has autostart enabled", agent_id)
            return

        if not agent.vm_target_id:
            logger.warning("Autostart: agent %s has no VM target configured", agent.name)
            return

        vm = await db.get_vm_target(agent.vm_target_id)
        if not vm:
            logger.warning("Autostart: VM target not found for agent %s", agent.name)
            return

        # Check if already running
        all_sessions = await db.list_sessions(agent_id=agent_id)
        for sess in all_sessions:
            if not sess.ticket_id and sess.status in ("running", "blocked"):
                logger.info("Autostart: agent %s already has running session", agent.name)
                return

        # Build SSH spec
        spec = build_ssh_spec(vm, agent.required_ssh_options)

        # Check that required tools are available
        tools_ok, tools_error = ensure_vm_tools(spec, agent.command, agent.session_mode)
        if not tools_ok:
            logger.warning("Autostart: tools check failed for agent %s: %s", agent.name, tools_error)
            return

        # Memory check before starting agent
        await db.refresh_agent_average_memory_usage(agent.id)
        agent = await db.get_agent(agent.id) # Refresh to get updated memory avg
        if agent and vm.required_reserve_memory_gb > 0:
            mem_ok, mem_error = check_vm_memory_available(spec, vm, agent)
            if not mem_ok:
                logger.warning("Autostart: memory check failed for agent %s: %s", agent.name, mem_error)
                return

        # Determine workspace: use working_directory if set, otherwise create temp
        if agent.working_directory:
            # Verify the working directory exists on the VM
            check_cmd = [
                "ssh", "-p",
                str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"test -d {shlex.quote(agent.working_directory)} && echo exists"
            ]
            result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=30)
            if result.stdout.strip() != "exists":
                logger.warning("Autostart: working directory %s does not exist for agent %s", agent.working_directory, agent.name)
                return
            workspace = agent.working_directory
        else:
            # Create temp workspace on VM target
            mktemp_cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}", f"mktemp -d /tmp/agent_{agent.slug}_XXXXXXXX"]
            result = subprocess.run(mktemp_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode != 0:
                logger.warning("Autostart: failed to create workspace for agent %s: %s", agent.name, result.stderr)
                return
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

        # Copy session files if configured (use_wintermute mode - no timestamp check)
        if agent.session_file_config_id:
            definitions = await db.list_session_file_definitions(agent.session_file_config_id)
            session_files = await db.list_session_files(agent_id)
            file_map = {sf.definition_id: sf for sf in session_files}

            with tempfile.TemporaryDirectory() as tmpdir:
                files_to_copy = []
                for defn in definitions:
                    sf = file_map.get(defn.id)
                    content = sf.content if sf else defn.default_content
                    local_path = f"{tmpdir}/{defn.filename}"
                    with open(local_path, "w") as f:
                        f.write(content)
                    files_to_copy.append(local_path)

                # Write SKILLS.md from template
                import os
                skills_template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "skills.md.template")
                if os.path.exists(skills_template_path):
                    with open(skills_template_path, "r") as f:
                        skills_content = f.read()
                    skills_path = f"{tmpdir}/SKILLS.md"
                    with open(skills_path, "w") as f:
                        f.write(skills_content)
                    files_to_copy.append(skills_path)

                if files_to_copy:
                    scp_cmd = ["scp", "-P", str(spec.port), *spec.options, *files_to_copy, f"{spec.user}@{spec.host}:{session_files_dir}/"]
                    scp_result = subprocess.run(scp_cmd, capture_output=True, text=True, timeout=60)
                    if scp_result.returncode != 0:
                        logger.warning("Autostart: failed to copy session files for agent %s: %s", agent.name, scp_result.stderr)

        # Create the session
        session_id = str(uuid.uuid4())
        default_initial_prompt = "Read your AGENTS.md file and then wait for further instructions."
        initial_prompt = agent.initial_prompt or default_initial_prompt

        # For claude/gemini modes, don't start a tmux session - just create the session record
        # with the initial prompt as prompt_pending. The session source will handle the actual
        # Claude/Gemini process via claude_client/gemini_client.
        if agent.session_mode in ("claude", "gemini"):
            await db.insert_session(
                session_id=session_id,
                project_id=None,
                agent_id=agent_id,
                ticket_id=None,
                status="running",
                repo_path=workspace,
                thread_ts=None,
                initial_prompt=initial_prompt,
                workspace_path=workspace,
            )
            # Set prompt_pending via update since insert_session doesn't support it
            await db.update_session(session_id, prompt_pending=initial_prompt)
            logger.info("Autostart: started session %s for agent %s", session_id, agent.name)
        else:
            # For tmux/mcp modes, use the traditional session start
            await db.insert_session(
                session_id=session_id,
                project_id=None,
                agent_id=agent_id,
                ticket_id=None,
                status="running",
                repo_path=workspace,
                thread_ts=None,
                initial_prompt=initial_prompt,
                workspace_path=workspace,
            )

            # Start the session
            try:
                start_session(spec, session_id, agent, workspace)
                logger.info("Autostart: started session %s for agent %s", session_id, agent.name)
            except Exception as exc:
                logger.error("Autostart: failed to start session for agent %s: %s", agent.name, exc)
                await db.update_session(session_id, status="failed")
                return

        # Create initial prompt comment (use correct insert_comment params)
        await db.insert_comment(
            comment_id=str(uuid.uuid4()),
            ticket_id=None,
            session_id=None,
            project_id=None,
            agent_id=agent_id,
            author="autostart",
            source_id=None,
            issue_number=None,
            body=initial_prompt,
            public=False,
            agent_session_id=session_id,
        )


class AutostartSource(TaskSource):
    id = "autostart"
    enabled = True
    base_priority = 50
    poll_interval_seconds = 300 # 5 minutes

    async def poll(self, ctx: dict[str, Any]) -> list[WorkItemDraft]:
        db: AsyncDatabase = ctx["db"]
        source = await db.get_task_source(self.id)

        # Use source config if exists, otherwise use defaults
        if source:
            if not source.enabled:
                return []
            priority = source.base_priority
        else:
            priority = self.base_priority

        # Find all agents with autostart=True
        agents = await db.list_agents()
        autostart_agents = [a for a in agents if a.autostart]

        if not autostart_agents:
            return []

        drafts = []
        for agent in autostart_agents:
            # Skip if no VM target
            if not agent.vm_target_id:
                continue

            # Check if already running
            all_sessions = await db.list_sessions(agent_id=agent.id)
            is_running = any(not sess.ticket_id and sess.status in ("running", "blocked") for sess in all_sessions)
            if is_running:
                continue

            # Create work item to start this agent
            work_id = f"autostart:{agent.id}:{utc_now()}"
            drafts.append(WorkItemDraft(
                work_id=work_id,
                priority=priority,
                source_id=self.id,
                checkpoint={
                    "agent_id": agent.id,
                    "agent_name": agent.name,
                },
            ))

        return drafts

    async def build_work_item(self, ctx: dict[str, Any], record: Any) -> WorkItem:
        return AutostartWorkItem(
            work_id=record.work_id,
            priority=record.priority,
            source_id=record.source_id,
            checkpoint=record.checkpoint,
        )
