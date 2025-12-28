"""SSH and screen-based agent session runner."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional
import logging

from wintermute.db import AgentRecord, AgentSessionRecord, ProjectVMRecord, VMTargetRecord


@dataclass(frozen=True)
class SSHSpec:
    host: str
    user: str
    port: int
    options: list[str]


def build_ssh_spec(vm: VMTargetRecord, extra_options: Optional[str]) -> SSHSpec:
    options = []
    if extra_options:
        options = shlex.split(extra_options)
    return SSHSpec(host=vm.host, user=vm.user, port=vm.port, options=options)


def _run_ssh(spec: SSHSpec, remote_args: list[str]) -> subprocess.CompletedProcess:
    logger = logging.getLogger(__name__)
    cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}"]
    cmd.extend(remote_args)
    logger.info("SSH run %s %s", spec.host, remote_args[0] if remote_args else "")
    return subprocess.run(cmd, check=False, capture_output=True)


def _screen_name(session_id: str) -> str:
    return f"wm_{session_id}"


def _log_path(session_id: str) -> str:
    return f"/tmp/wintermute-{session_id}.log"


def _escape_for_ansic(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def ensure_repo(spec: SSHSpec, project_vm: ProjectVMRecord) -> Optional[str]:
    if project_vm.repo_mode == "mirror":
        return project_vm.repo_path
    if project_vm.repo_mode == "clone":
        if not project_vm.repo_path or not project_vm.repo_url:
            return None
        clone_cmd = (
            "if [ ! -d {path} ]; then git clone {url} {path}; fi"
        ).format(
            path=shlex.quote(project_vm.repo_path),
            url=shlex.quote(project_vm.repo_url),
        )
        _run_ssh(spec, ["bash", "-lc", clone_cmd])
        return project_vm.repo_path
    return None


def start_session(
    spec: SSHSpec,
    session_id: str,
    agent: AgentRecord,
    repo_path: str,
) -> None:
    logger = logging.getLogger(__name__)
    name = _screen_name(session_id)
    logfile = _log_path(session_id)
    cmd = (
        "cd {repo} && screen -S {name} -dmL -Logfile {log} bash -lc {agent_cmd}"
    ).format(
        repo=shlex.quote(repo_path),
        name=shlex.quote(name),
        log=shlex.quote(logfile),
        agent_cmd=shlex.quote(agent.command),
    )
    logger.info("Starting session %s in %s", session_id, repo_path)
    _run_ssh(spec, ["bash", "-lc", cmd])


def send_input(spec: SSHSpec, session: AgentSessionRecord, text: str) -> None:
    logger = logging.getLogger(__name__)
    name = _screen_name(session.id)
    payload = _escape_for_ansic(text) + "\\n"
    cmd = f"screen -S {shlex.quote(name)} -X stuff $'{payload}'"
    logger.info("Sending input to session %s (%d chars)", session.id, len(text))
    _run_ssh(spec, ["bash", "-lc", cmd])


def is_session_running(spec: SSHSpec, session_id: str) -> bool:
    name = _screen_name(session_id)
    cmd = f"screen -ls | grep -F {shlex.quote(name)}"
    result = _run_ssh(spec, ["bash", "-lc", cmd])
    return result.returncode == 0


def command_exists(spec: SSHSpec, command: str) -> bool:
    cmd = f"command -v {shlex.quote(command)}"
    result = _run_ssh(spec, ["bash", "-lc", cmd])
    return result.returncode == 0


def read_output(
    spec: SSHSpec, session: AgentSessionRecord, max_bytes: int = 32768
) -> tuple[str, int]:
    logger = logging.getLogger(__name__)
    logfile = _log_path(session.id)
    offset = session.last_output_offset
    cmd = (
        "if [ -f {log} ]; then tail -c +{start} {log} | head -c {limit}; fi"
    ).format(
        log=shlex.quote(logfile),
        start=offset + 1,
        limit=max_bytes,
    )
    result = _run_ssh(spec, ["bash", "-lc", cmd])
    data = result.stdout or b""
    text = data.decode("utf-8", errors="ignore")
    if text:
        logger.info("Read %d bytes from session %s", len(text), session.id)
    return text, offset + len(data)
