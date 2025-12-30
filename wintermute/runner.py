"""SSH and screen-based agent session runner."""

from __future__ import annotations

import getpass
import logging
import os
import re
import shlex
import socket
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


def parse_ssh_options(extra_options: Optional[str]) -> list[str]:
    if not extra_options:
        return []
    return shlex.split(extra_options)


def strip_port_forwards(options: list[str]) -> list[str]:
    filtered: list[str] = []
    skip_next = False
    for item in options:
        if skip_next:
            skip_next = False
            continue
        if item in {"-L", "-R", "-D"}:
            skip_next = True
            continue
        if item.startswith("-L") or item.startswith("-R") or item.startswith("-D"):
            continue
        filtered.append(item)
    return filtered


def build_ssh_spec(vm: VMTargetRecord, extra_options: Optional[str]) -> SSHSpec:
    return build_ssh_spec_with_options(vm, parse_ssh_options(extra_options))


def build_ssh_spec_with_options(vm: VMTargetRecord, options: list[str]) -> SSHSpec:
    return SSHSpec(host=vm.host, user=vm.user, port=vm.port, options=options)


def _is_local_host(spec: SSHSpec) -> bool:
    host = spec.host.lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        return True
    hostname = socket.gethostname().lower()
    fqdn = socket.getfqdn().lower()
    if host in {hostname, fqdn, f"{hostname}.local", f"{fqdn}.local"}:
        return True
    return False


def _run_local(
    remote_args: list[str],
    timeout: Optional[int],
    input_data: Optional[str] = None,
) -> subprocess.CompletedProcess:
    logger = logging.getLogger(__name__)
    logger.info("Local run %s", remote_args[0] if remote_args else "")
    try:
        return subprocess.run(
            remote_args,
            check=False,
            capture_output=True,
            timeout=timeout,
            input=input_data,
            text=bool(input_data is not None),
        )
    except subprocess.TimeoutExpired as exc:
        logger.error("Local command timed out after %s seconds", timeout)
        return subprocess.CompletedProcess(exc.cmd, 124, stdout=b"", stderr=b"Command timed out")


def _run_ssh(
    spec: SSHSpec, remote_args: list[str], timeout: Optional[int] = None
) -> subprocess.CompletedProcess:
    logger = logging.getLogger(__name__)
    if _is_local_host(spec) and spec.user == getpass.getuser():
        return _run_local(remote_args, timeout)
    remote_cmd = " ".join(shlex.quote(arg) for arg in remote_args)
    cmd = [
        "ssh",
        "-p",
        str(spec.port),
        *spec.options,
        f"{spec.user}@{spec.host}",
        remote_cmd,
    ]
    logger.info("SSH run %s %s", spec.host, remote_args[0] if remote_args else "")
    try:
        return subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        logger.error("SSH command timed out after %s seconds", timeout)
        return subprocess.CompletedProcess(exc.cmd, 124, stdout=b"", stderr=b"Command timed out")


def _run_ssh_script(
    spec: SSHSpec, script: str, timeout: Optional[int] = None
) -> subprocess.CompletedProcess:
    if _is_local_host(spec) and spec.user == getpass.getuser():
        return _run_local(["bash", "-s"], timeout, input_data=script)
    cmd = [
        "ssh",
        "-p",
        str(spec.port),
        *spec.options,
        f"{spec.user}@{spec.host}",
        "bash -s",
    ]
    try:
        return subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=timeout,
            input=script,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        logger = logging.getLogger(__name__)
        logger.error("SSH script timed out after %s seconds", timeout)
        return subprocess.CompletedProcess(exc.cmd, 124, stdout=b"", stderr=b"Command timed out")


def _screen_name(session_id: str) -> str:
    return f"wm_{session_id}"


def _log_path(session_id: str) -> str:
    return f"/tmp/wintermute-{session_id}.log"


def _escape_for_ansic(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "\\'")


def _stderr_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def _stdout_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")
    return str(value)


def ensure_repo(spec: SSHSpec, project_vm: ProjectVMRecord) -> Optional[str]:
    logger = logging.getLogger(__name__)
    if project_vm.repo_mode == "mirror":
        if not project_vm.repo_path:
            return None
        check_cmd = f"test -d {shlex.quote(project_vm.repo_path)}"
        result = _run_ssh(spec, ["bash", "-lc", check_cmd], timeout=20)
        if result.returncode != 0:
            raise RuntimeError(f"Mirror path not found on VM: {project_vm.repo_path}")
        return project_vm.repo_path
    if project_vm.repo_mode == "clone":
        if not project_vm.repo_path or not project_vm.repo_url:
            return None
        parent_dir = os.path.dirname(project_vm.repo_path)
        logger.info(
            "Ensuring repo clone path=%s parent=%s url=%s",
            project_vm.repo_path,
            parent_dir,
            project_vm.repo_url,
        )
        if not parent_dir:
            parent_dir = "."
        mkdir_cmd = "mkdir -p {parent}".format(parent=shlex.quote(parent_dir))
        logger.info("Repo clone mkdir command: %s", mkdir_cmd)
        result = _run_ssh_script(spec, f"{mkdir_cmd}\n", timeout=30)
        if result.returncode != 0:
            stderr = _stderr_text(result.stderr).strip()
            stdout = _stdout_text(result.stdout).strip()
            logger.error("Repo mkdir failed: %s", stderr or "unknown error")
            detail = stderr or "Repo mkdir failed"
            if stdout:
                detail = f"{detail} stdout={stdout}"
            raise RuntimeError(
                f"{detail} (code={result.returncode} repo_path={project_vm.repo_path} parent={parent_dir} cmd={mkdir_cmd})"
            )
        clone_cmd = "if [ ! -d {path} ]; then git clone {url} {path}; fi".format(
            path=shlex.quote(project_vm.repo_path),
            url=shlex.quote(project_vm.repo_url),
        )
        logger.info("Repo clone command: %s", clone_cmd)
        result = _run_ssh_script(spec, f"{clone_cmd}\n", timeout=300)
        if result.returncode != 0:
            stderr = _stderr_text(result.stderr).strip()
            stdout = _stdout_text(result.stdout).strip()
            logger.error("Repo clone failed: %s", stderr or "unknown error")
            detail = stderr or "Repo clone failed"
            if stdout:
                detail = f"{detail} stdout={stdout}"
            raise RuntimeError(
                f"{detail} (code={result.returncode} repo_path={project_vm.repo_path} parent={parent_dir} cmd={clone_cmd})"
            )
        return project_vm.repo_path
    return None


def prepare_issue_branch(spec: SSHSpec, repo_path: str, issue_number: int) -> str:
    logger = logging.getLogger(__name__)
    branch = f"issue{issue_number}"
    cmd = (
        "set -e; cd {repo}; "
        "git fetch origin --prune; "
        "default=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@'); "
        "if [ -z \"$default\" ]; then "
        "  if git show-ref --verify --quiet refs/remotes/origin/main; then default=main; "
        "  elif git show-ref --verify --quiet refs/remotes/origin/master; then default=master; "
        "  else default=main; fi; "
        "fi; "
        "if git show-ref --verify --quiet refs/heads/$default; then "
        "  git checkout $default; "
        "else "
        "  git checkout -b $default origin/$default; "
        "fi; "
        "git pull --ff-only origin $default || true; "
        "if git show-ref --verify --quiet refs/heads/{branch}; then "
        "  git checkout {branch}; "
        "else "
        "  git checkout -b {branch}; "
        "fi"
    ).format(
        repo=shlex.quote(repo_path),
        branch=shlex.quote(branch),
    )
    result = _run_ssh_script(spec, f"{cmd}\n", timeout=120)
    if result.returncode != 0:
        stderr = _stderr_text(result.stderr).strip()
        logger.error("Branch prep failed: %s", stderr or "unknown error")
        raise RuntimeError(stderr or "Branch prep failed")
    return branch


def prepare_ticket_branch(spec: SSHSpec, repo_path: str, ticket_id: str) -> str:
    logger = logging.getLogger(__name__)
    safe_id = re.sub(r"[^a-zA-Z0-9]+", "-", ticket_id.strip().lower()).strip("-")
    short_id = safe_id[:10] if safe_id else "ticket"
    branch = f"ticket-{short_id}"
    cmd = (
        "set -e; cd {repo}; "
        "git fetch origin --prune; "
        "default=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's@^refs/remotes/origin/@@'); "
        "if [ -z \"$default\" ]; then "
        "  if git show-ref --verify --quiet refs/remotes/origin/main; then default=main; "
        "  elif git show-ref --verify --quiet refs/remotes/origin/master; then default=master; "
        "  else default=main; fi; "
        "fi; "
        "if git show-ref --verify --quiet refs/heads/$default; then "
        "  git checkout $default; "
        "else "
        "  git checkout -b $default origin/$default; "
        "fi; "
        "git pull --ff-only origin $default || true; "
        "if git show-ref --verify --quiet refs/heads/{branch}; then "
        "  git checkout {branch}; "
        "else "
        "  git checkout -b {branch}; "
        "fi"
    ).format(
        repo=shlex.quote(repo_path),
        branch=shlex.quote(branch),
    )
    result = _run_ssh_script(spec, f"{cmd}\n", timeout=120)
    if result.returncode != 0:
        stderr = _stderr_text(result.stderr).strip()
        logger.error("Branch prep failed: %s", stderr or "unknown error")
        raise RuntimeError(stderr or "Branch prep failed")
    return branch


def start_session(
    spec: SSHSpec,
    session_id: str,
    agent: AgentRecord,
    repo_path: str,
) -> None:
    logger = logging.getLogger(__name__)
    name = _screen_name(session_id)
    logfile = _log_path(session_id)
    agent_cmd = agent.command
    if agent.env_vars:
        env_vars = agent.env_vars.strip()
        if env_vars:
            agent_cmd = f"env {env_vars} {agent.command}"
    cmd = (
        "cd {repo} && screen -S {name} -dmL -Logfile {log} bash -lc {agent_cmd}"
    ).format(
        repo=shlex.quote(repo_path),
        name=shlex.quote(name),
        log=shlex.quote(logfile),
        agent_cmd=shlex.quote(agent_cmd),
    )
    logger.info("Starting session %s in %s", session_id, repo_path)
    _run_ssh(spec, ["bash", "-lc", cmd], timeout=30)


def send_input(spec: SSHSpec, session: AgentSessionRecord, text: str) -> None:
    logger = logging.getLogger(__name__)
    name = _screen_name(session.id)
    trimmed = text.rstrip("\r\n")
    payload = _escape_for_ansic(trimmed)
    cmd = "\n".join(
        [
            f"screen -S {shlex.quote(name)} -p 0 -X stuff $'{payload}'",
            f"screen -S {shlex.quote(name)} -p 0 -X stuff $'\\015'",
        ]
    )
    logger.info("Sending input to session %s (%d chars)", session.id, len(text))
    result = _run_ssh_script(spec, f"{cmd}\n", timeout=30)
    if result.returncode != 0:
        stderr = _stderr_text(result.stderr).strip()
        logger.error("Failed to send input to session %s: %s", session.id, stderr or "unknown error")


def send_interrupt(spec: SSHSpec, session_id: str, count: int = 2) -> None:
    logger = logging.getLogger(__name__)
    name = _screen_name(session_id)
    payload = "\\003" * max(count, 1)
    cmd = f"screen -S {shlex.quote(name)} -X stuff $'{payload}'"
    logger.info("Sending interrupt to session %s (x%d)", session_id, count)
    _run_ssh_script(spec, f"{cmd}\n", timeout=10)


def stop_session(spec: SSHSpec, session_id: str, count: int = 2) -> None:
    logger = logging.getLogger(__name__)
    name = _screen_name(session_id)
    send_interrupt(spec, session_id, count=count)
    quit_cmd = f"screen -S {shlex.quote(name)} -X quit"
    logger.info("Requesting screen quit for session %s", session_id)
    _run_ssh_script(spec, f"{quit_cmd}\n", timeout=10)


def is_session_running(spec: SSHSpec, session_id: str) -> bool:
    name = _screen_name(session_id)
    cmd = f"screen -ls | grep -F {shlex.quote(name)}"
    result = _run_ssh(spec, ["bash", "-lc", cmd], timeout=15)
    return result.returncode == 0


def command_exists(spec: SSHSpec, command: str) -> bool:
    cmd = f"command -v {shlex.quote(command)}"
    result = _run_ssh(spec, ["bash", "-lc", cmd], timeout=10)
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
    result = _run_ssh(spec, ["bash", "-lc", cmd], timeout=10)
    data = result.stdout or b""
    text = data.decode("utf-8", errors="ignore")
    if text:
        logger.info("Read %d bytes from session %s", len(text), session.id)
    return text, offset + len(data)
