#!/usr/bin/env python3
"""Sanity check Wintermute configuration for automated GitHub issue sessions."""

from __future__ import annotations

import os
import shlex
import sys
from typing import Iterable

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from wintermute.services.database import Database
from wintermute.runner import build_ssh_spec, command_exists


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            key = key.strip()
            value = value.strip().strip("'\"")
            if key and key not in os.environ:
                os.environ[key] = value


def _first_token(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return ""
    return parts[0] if parts else ""


def _format_list(items: Iterable[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def main() -> int:
    _load_dotenv()
    db = Database()
    db.initialize()

    errors: list[str] = []
    warnings: list[str] = []

    slack_source = db.get_task_source("slack")
    if not slack_source or not slack_source.enabled:
        warnings.append("Slack source is disabled.")

    github_source = db.get_task_source("github_issues")
    if not github_source or not github_source.enabled:
        warnings.append("GitHub issues poller is disabled.")

    github_sources = [source for source in db.list_github_sources() if source.enabled]
    if not github_sources:
        warnings.append("No enabled GitHub sources found.")

    for source in github_sources:
        project = db.get_project(source.project_id)
        if not project:
            errors.append(f"GitHub source {source.id} has no valid project.")
            continue
        if source.auto_start:
            if not source.agent_id:
                errors.append(f"GitHub source {source.id} has auto-start enabled but no agent.")
                continue
            agent = db.get_agent(source.agent_id)
            if not agent:
                errors.append(f"GitHub source {source.id} references missing agent.")
                continue
            project_vm = db.get_project_vm_for_project(project.id)
            if not project_vm:
                errors.append(f"Project {project.name} has no VM mapping.")
                continue
            vm = db.get_vm_target(project_vm.vm_target_id)
            if not vm:
                errors.append(f"Project {project.name} VM target missing.")
                continue
            if project_vm.repo_mode == "clone":
                if not project_vm.repo_url or not project_vm.repo_path:
                    errors.append(f"Project {project.name} VM mapping missing clone repo_url or repo_path.")
            elif project_vm.repo_mode == "mirror":
                if not project_vm.repo_path:
                    errors.append(f"Project {project.name} VM mapping missing repo_path.")
            else:
                warnings.append(f"Project {project.name} has unknown repo mode {project_vm.repo_mode}.")
            if not project.slack_channel_id:
                warnings.append(f"Project {project.name} has no Slack channel ID.")
            command = _first_token(agent.command)
            if not command:
                warnings.append(f"Agent {agent.name} has an empty command.")
            else:
                spec = build_ssh_spec(vm, agent.required_ssh_options)
                if not command_exists(spec, command):
                    warnings.append(f"Command '{command}' not found on {vm.host} for agent {agent.name}.")

    if warnings:
        print("WARNINGS:")
        print(_format_list(warnings))
    if errors:
        print("ERRORS:")
        print(_format_list(errors))
        return 1
    print("OK: sanity check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
