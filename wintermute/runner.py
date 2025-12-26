"""SSH and screen-based agent session runner."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from typing import Optional

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
    cmd = ["ssh", "-p", str(spec.port), *spec.options, f"{spec.user}@{spec.host}"]
    cmd.extend(remote_args)
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
    _run_ssh(spec, ["bash", "-lc", cmd])


def send_input(spec: SSHSpec, session: AgentSessionRecord, text: str) -> None:
    name = _screen_name(session.id)
    payload = _escape_for_ansic(text) + "\\n"
    cmd = f"screen -S {shlex.quote(name)} -X stuff $'{payload}'"
    _run_ssh(spec, ["bash", "-lc", cmd])


def read_output(
    spec: SSHSpec, session: AgentSessionRecord, max_bytes: int = 32768
) -> tuple[str, int]:
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
    return text, offset + len(data)
