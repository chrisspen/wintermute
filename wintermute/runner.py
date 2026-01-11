"""SSH and tmux-based agent session runner."""

from __future__ import annotations

import getpass
import logging
import os
import re
import shlex
import socket
import subprocess
import urllib.parse
from dataclasses import dataclass
from typing import Optional

from wintermute.db import AgentRecord, AgentSessionRecord, ProjectRecord, VMTargetRecord


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
    logger.debug("SSH run %s %s", spec.host, remote_args[0] if remote_args else "")
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


def _session_name(session_id: str) -> str:
    return f"wm_{session_id}"


def _log_path(session_id: str) -> str:
    return f"/tmp/wintermute-{session_id}.log"


def is_codex_command(command: str) -> bool:
    if not command:
        return False
    try:
        parts = shlex.split(command)
    except ValueError:
        parts = command.split()
    if not parts:
        return False
    return os.path.basename(parts[0]) == "codex"


def set_codex_trust(spec: SSHSpec, repo_path: str, trust_level: str) -> None:
    if not repo_path or not trust_level:
        return
    safe_repo = repo_path.replace("'", "'\"'\"'")
    safe_level = trust_level.replace("'", "'\"'\"'")
    script = f"""
set -e
config="$HOME/.codex/config.toml"
mkdir -p "$(dirname "$config")"
if [ ! -f "$config" ]; then
  : > "$config"
fi
tmp="$(mktemp)"
awk -v target='[projects."{safe_repo}"]' -v level="{safe_level}" '
  BEGIN {{ skip=0 }}
  {{
    if ($0 ~ /^\\[/) {{
      if (skip) {{ skip=0 }}
      if ($0 == target) {{ skip=1; next }}
    }}
    if (!skip) print $0
  }}
  END {{
    print ""
    print target
    print "trust_level = \\"" level "\\""
  }}
' "$config" > "$tmp"
mv "$tmp" "$config"
"""
    _run_ssh_script(spec, script, timeout=10)


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


def _session_repo_path(project: ProjectRecord, session_id: Optional[str]) -> Optional[str]:
    """Compute the repo path for a session. Repo config comes from Project."""
    if not project.repo_path:
        return None
    repo_mode = project.repo_mode or "mirror"
    if repo_mode != "clone":
        return project.repo_path
    if not session_id:
        return project.repo_path
    safe_suffix = re.sub(r"[^a-zA-Z0-9]+", "-", session_id.strip().lower()).strip("-")
    if not safe_suffix:
        return project.repo_path
    return f"{project.repo_path}-{safe_suffix}"


def ensure_repo(
    spec: SSHSpec,
    project: ProjectRecord,
    session_id: Optional[str] = None,
    repo_path: Optional[str] = None,
) -> Optional[str]:
    """Ensure the repo exists on the VM. Repo config comes from Project."""
    logger = logging.getLogger(__name__)
    repo_path = repo_path or _session_repo_path(project, session_id)
    repo_mode = project.repo_mode or "mirror"
    if repo_mode == "mirror":
        if not repo_path:
            return None
        check_cmd = f"test -d {shlex.quote(repo_path)}"
        result = _run_ssh(spec, ["bash", "-lc", check_cmd], timeout=20)
        if result.returncode != 0:
            raise RuntimeError(f"Mirror path not found on VM: {repo_path}")
        return repo_path
    if repo_mode == "clone":
        if not repo_path or not project.repo_url:
            return None
        parent_dir = os.path.dirname(repo_path)
        logger.info(
            "Ensuring repo clone path=%s parent=%s url=%s",
            repo_path,
            parent_dir,
            project.repo_url,
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
                f"{detail} (code={result.returncode} repo_path={repo_path} parent={parent_dir} cmd={mkdir_cmd})"
            )
        clone_cmd = (
            "if [ -d {path}/.git ]; then "
            "  cd {path}; git fetch origin --prune; "
            "elif [ -d {path} ]; then "
            "  echo 'Repo path exists but is not a git repo' >&2; exit 2; "
            "else "
            "  git clone {url} {path}; "
            "fi"
        ).format(
            path=shlex.quote(repo_path),
            url=shlex.quote(project.repo_url),
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
                f"{detail} (code={result.returncode} repo_path={repo_path} parent={parent_dir} cmd={clone_cmd})"
            )
        return repo_path
    return None


def configure_git_push_auth(
    spec: SSHSpec,
    repo_path: str,
    repo_url: Optional[str],
    token: Optional[str],
    *,
    username: str = "x-access-token",
) -> None:
    if not repo_path or not repo_url or not token:
        return
    parsed = urllib.parse.urlparse(repo_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return
    safe_token = token.replace("'", "'\"'\"'")
    safe_user = username.replace("'", "'\"'\"'")
    push_netloc = f"{safe_user}:{safe_token}@{parsed.netloc}"
    push_url = urllib.parse.urlunparse(parsed._replace(netloc=push_netloc))
    script = (
        "set -e; "
        f"cd {shlex.quote(repo_path)}; "
        f"git remote set-url --push origin {shlex.quote(push_url)};"
    )
    _run_ssh_script(spec, f"{script}\n", timeout=20)


def delete_repo_path(spec: SSHSpec, repo_path: str) -> None:
    if not repo_path or repo_path.strip() in {"/", "."}:
        raise ValueError("Refusing to delete unsafe repo path")
    cleaned = repo_path.strip()
    if len(cleaned) < 4:
        raise ValueError("Refusing to delete short repo path")
    cmd = f"rm -rf {shlex.quote(cleaned)}"
    result = _run_ssh_script(spec, f"{cmd}\n", timeout=120)
    if result.returncode != 0:
        stderr = _stderr_text(result.stderr).strip()
        stdout = _stdout_text(result.stdout).strip()
        detail = stderr or "Repo delete failed"
        if stdout:
            detail = f"{detail} stdout={stdout}"
        raise RuntimeError(detail)


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
    name = _session_name(session_id)
    logfile = _log_path(session_id)
    agent_cmd = agent.command
    if agent.env_vars:
        env_vars = agent.env_vars.strip()
        if env_vars:
            agent_cmd = f"env {env_vars} {agent.command}"
    cmd = "\n".join(
        [
            f"tmux new-session -d -s {shlex.quote(name)} -c {shlex.quote(repo_path)}",
            f"tmux set-option -t {shlex.quote(name)} prefix C-a",
            f"tmux unbind-key -t {shlex.quote(name)} C-b",
            f"tmux bind-key -t {shlex.quote(name)} C-a send-prefix",
            f"tmux send-keys -t {shlex.quote(name)} \"bash -lc {shlex.quote(agent_cmd)}\" Enter",
            f"tmux pipe-pane -o -t {shlex.quote(name)} \"cat >> {shlex.quote(logfile)}\"",
        ]
    )
    logger.info("Starting session %s in %s", session_id, repo_path)
    _run_ssh_script(spec, f"{cmd}\n", timeout=30)


def send_input(spec: SSHSpec, session: AgentSessionRecord, text: str) -> None:
    logger = logging.getLogger(__name__)
    name = _session_name(session.id)
    trimmed = text.rstrip("\r\n")
    cmd = "\n".join(
        [
            f"tmux send-keys -t {shlex.quote(name)} -l {shlex.quote(trimmed)}",
            f"tmux send-keys -t {shlex.quote(name)} C-m",
            f"tmux send-keys -t {shlex.quote(name)} C-j",
        ]
    )
    logger.info("Sending input to session %s (%d chars)", session.id, len(text))
    result = _run_ssh_script(spec, f"{cmd}\n", timeout=30)
    if result.returncode != 0:
        stderr = _stderr_text(result.stderr).strip()
        stdout = _stdout_text(result.stdout).strip()
        logger.error(
            "Failed to send input to session %s: %s stdout=%s",
            session.id,
            stderr or "unknown error",
            stdout or "none",
        )


def send_interrupt(spec: SSHSpec, session_id: str, count: int = 2) -> None:
    logger = logging.getLogger(__name__)
    name = _session_name(session_id)
    count = max(count, 1)
    cmd = " ".join(
        [f"tmux send-keys -t {shlex.quote(name)} C-c" for _ in range(count)]
    )
    logger.info("Sending interrupt to session %s (x%d)", session_id, count)
    _run_ssh_script(spec, f"{cmd}\n", timeout=10)


def stop_session(spec: SSHSpec, session_id: str, count: int = 2) -> None:
    logger = logging.getLogger(__name__)
    name = _session_name(session_id)
    send_interrupt(spec, session_id, count=count)
    quit_cmd = f"tmux kill-session -t {shlex.quote(name)}"
    logger.info("Requesting tmux kill for session %s", session_id)
    _run_ssh_script(spec, f"{quit_cmd}\n", timeout=10)


def is_session_running(spec: SSHSpec, session_id: str) -> bool:
    name = _session_name(session_id)
    cmd = f"tmux has-session -t {shlex.quote(name)}"
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
