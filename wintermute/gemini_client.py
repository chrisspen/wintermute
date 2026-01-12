"""Gemini CLI client for Wintermute agent sessions.

Unlike Claude, Gemini CLI doesn't support persistent subprocess mode with
continuous JSON I/O. Each prompt requires a new process invocation where
the prompt is piped via stdin.

Launch: echo "prompt" | gemini --output-format stream-json -y
"""

from __future__ import annotations

import getpass
import json
import logging
import os
import selectors
import shlex
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

from wintermute.db import AgentRecord
from wintermute.runner import SSHSpec


@dataclass(frozen=True)
class GeminiResult:
    """Result from a Gemini CLI invocation."""

    response_text: str
    session_id: Optional[str]
    usage: Optional[dict[str, Any]] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None


@dataclass
class GeminiSession:
    """Tracks Gemini session state across invocations."""

    session_id: Optional[str] = None
    last_used: float = 0.0


_GEMINI_LOCK = threading.Lock()
_GEMINI_SESSIONS: dict[str, GeminiSession] = {}


def _is_local_host(host: str) -> bool:
    """Check if host refers to the local machine."""
    value = host.strip().lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    hostname = socket.gethostname().lower()
    fqdn = socket.getfqdn().lower()
    return value in {hostname, fqdn, f"{hostname}.local", f"{fqdn}.local"}


def _build_gemini_command(agent: AgentRecord, cwd: str, resume_session: Optional[str] = None) -> list[str]:
    """Build the Gemini CLI command arguments."""
    cmd = agent.command.strip() if agent.command else "gemini"

    # Core flags for streaming JSON mode with auto-approval
    args = [cmd, "--output-format", "stream-json", "-y"]

    # Resume previous session if we have one
    if resume_session:
        args.extend(["--resume", resume_session])

    # Parse additional config from agent.mcp_config
    if agent.mcp_config:
        config = agent.mcp_config.strip()
        if config:
            try:
                extra_args = shlex.split(config)
                args.extend(extra_args)
            except ValueError:
                args.extend(config.split())

    return args


def _build_gemini_shell(agent: AgentRecord, cwd: str, resume_session: Optional[str] = None) -> str:
    """Build the shell command to launch Gemini."""
    args = _build_gemini_command(agent, cwd, resume_session)
    cmd_str = shlex.join(args)

    if agent.env_vars:
        env_vars = agent.env_vars.strip()
        if env_vars:
            cmd_str = f"env {env_vars} {cmd_str}"

    # Wrap with profile sourcing for proper environment
    # Source nvm directly since .bashrc exits early for non-interactive shells
    wrapped = (
        "source ~/.profile >/dev/null 2>&1; "
        "source ~/.bash_profile >/dev/null 2>&1; "
        "export NVM_DIR=\"$HOME/.nvm\"; "
        "[ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\"; "
        f"cd {shlex.quote(cwd)}; "
        "if command -v stdbuf >/dev/null 2>&1; then "
        f"exec stdbuf -oL -eL {cmd_str}; "
        "else "
        f"exec {cmd_str}; "
        "fi"
    )
    return wrapped


def _run_gemini_process(
    spec: SSHSpec,
    agent: AgentRecord,
    cwd: str,
    prompt: str,
    resume_session: Optional[str] = None,
) -> subprocess.Popen:
    """Run a single Gemini invocation with the prompt piped to stdin."""
    shell_cmd = _build_gemini_shell(agent, cwd, resume_session)
    logger = logging.getLogger(__name__)
    local_host = _is_local_host(spec.host)
    current_user = getpass.getuser()

    if local_host and spec.user == current_user:
        logger.info(
            "Gemini local mode host=%s user=%s cwd=%s",
            spec.host,
            spec.user,
            cwd,
        )
        logger.info("Gemini local command: %s", shell_cmd)
        proc = subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
        )
        # Write prompt to stdin and close to signal end of input
        if proc.stdin:
            proc.stdin.write((prompt + "\n").encode("utf-8"))
            proc.stdin.close()
        return proc

    logger.info(
        "Gemini SSH mode host=%s user=%s current_user=%s cwd=%s",
        spec.host,
        spec.user,
        current_user,
        cwd,
    )
    ssh_cmd = [
        "ssh",
        "-T",
        "-o", "RequestTTY=no",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-p", str(spec.port),
        *spec.options,
        f"{spec.user}@{spec.host}",
        f"cd {shlex.quote(cwd)} && bash -lc {shlex.quote(shell_cmd)}",
    ]
    logger.info("Gemini SSH command: %s", " ".join(ssh_cmd))
    proc = subprocess.Popen(
        ssh_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )
    # Write prompt to stdin and close to signal end of input
    if proc.stdin:
        proc.stdin.write((prompt + "\n").encode("utf-8"))
        proc.stdin.close()
    return proc


def _get_gemini_log_path(session_id: str) -> str:
    """Get path for Gemini session log file."""
    base_dir = os.path.join(os.getcwd(), ".runtime", "logs")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"gemini_{session_id}.log")


def _append_gemini_log(log_path: str, entry: str) -> None:
    """Append an entry to the Gemini session log."""
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(entry + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Failed to write Gemini log entry")


def _read_stream_response(
    proc: subprocess.Popen,
    *,
    deadline: float,
    log_path: Optional[str] = None,
) -> tuple[str, Optional[str], Optional[str], Optional[dict[str, Any]]]:
    """Read streaming JSON response from Gemini.

    Gemini stream-json format:
    - {"type":"init","session_id":"...","model":"..."}
    - {"type":"message","role":"user","content":"..."}
    - {"type":"message","role":"assistant","content":"...","delta":true}
    - {"type":"result","status":"success","stats":{...}}

    Returns: (response_text, session_id, error, usage)
    """
    logger = logging.getLogger(__name__)
    if not proc.stdout:
        return "", None, "Gemini process stdout closed", None

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    if proc.stderr:
        selector.register(proc.stderr, selectors.EVENT_READ)

    buffer = ""
    texts: list[str] = []
    session_id: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[dict[str, Any]] = None

    done = False
    while time.time() < deadline and not done:
        remaining = max(deadline - time.time(), 0.1)
        events = selector.select(timeout=remaining)

        if not events:
            # Check if process has exited
            if proc.poll() is not None:
                break
            continue

        for key, _mask in events:
            is_stderr = proc.stderr is not None and key.fileobj is proc.stderr
            chunk = os.read(key.fileobj.fileno(), 4096)

            if not chunk:
                selector.close()
                return "".join(texts), session_id, error, usage

            text = chunk.decode("utf-8", errors="ignore")

            if is_stderr:
                if log_path:
                    _append_gemini_log(log_path, f"[stderr] {text.strip()}")
                # Gemini outputs info messages to stderr, ignore common ones
                lowered = text.lower()
                if "credentials" not in lowered and "yolo" not in lowered:
                    logger.warning("Gemini stderr: %s", text.strip())
                continue

            buffer += text

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if log_path:
                    _append_gemini_log(log_path, line)

                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Gemini non-JSON output: %s", line)
                    continue

                msg_type = payload.get("type")

                if msg_type == "init":
                    session_id = payload.get("session_id")
                    logger.info("Gemini session initialized: %s", session_id)

                elif msg_type == "message":
                    role = payload.get("role")
                    if role == "assistant":
                        content = payload.get("content", "")
                        if content:
                            # Gemini sends delta messages, accumulate them
                            texts.append(content)

                elif msg_type == "result":
                    usage = payload.get("stats")
                    status = payload.get("status")
                    if status != "success":
                        error = payload.get("error") or f"Gemini result status: {status}"
                    # Result message means the response is complete
                    done = True
                    break

    selector.close()
    # Join all delta texts into a single response
    return "".join(texts), session_id, error, usage


def get_gemini_session(wintermute_session_id: str) -> GeminiSession:
    """Get or create a Gemini session tracker."""
    with _GEMINI_LOCK:
        existing = _GEMINI_SESSIONS.get(wintermute_session_id)
        if existing:
            return existing

        session = GeminiSession(session_id=None, last_used=time.time())
        _GEMINI_SESSIONS[wintermute_session_id] = session
        return session


def close_gemini_process(wintermute_session_id: str) -> None:
    """Cleanup Gemini session state."""
    with _GEMINI_LOCK:
        _GEMINI_SESSIONS.pop(wintermute_session_id, None)


def poll_gemini(wintermute_session_id: str, timeout_seconds: int = 5) -> GeminiResult:
    """Poll for any pending output from Gemini.

    Since Gemini uses per-invocation processes, there's nothing to poll.
    This function exists for API compatibility with Claude/MCP clients.
    """
    return GeminiResult(
        response_text="",
        session_id=None,
        error=None,
    )


def run_gemini_prompt(
    spec: SSHSpec,
    agent: AgentRecord,
    *,
    session_id: str,
    prompt: str,
    cwd: str,
    timeout_seconds: int = 300,
) -> GeminiResult:
    """Send a prompt to Gemini and wait for response.

    Unlike Claude, Gemini CLI doesn't support persistent processes.
    Each call spawns a new process with the prompt piped to stdin.
    Session continuity is achieved using --resume flag with the Gemini session ID.

    Args:
        spec: SSH connection specification for the target VM
        agent: Agent configuration record
        session_id: Wintermute session ID (for session management)
        prompt: The prompt to send to Gemini
        cwd: Working directory for the Gemini session
        timeout_seconds: Response timeout in seconds

    Returns:
        GeminiResult with response text and session info
    """
    logger = logging.getLogger(__name__)
    log_path = _get_gemini_log_path(session_id)

    # Get session tracker for resume capability
    gemini_session = get_gemini_session(session_id)
    gemini_session.last_used = time.time()

    # Log the prompt
    _append_gemini_log(log_path, f"[prompt] {prompt}")

    # Run a single Gemini invocation with the prompt
    try:
        proc = _run_gemini_process(
            spec,
            agent,
            cwd,
            prompt,
            resume_session=gemini_session.session_id,
        )
    except Exception as e:
        error = f"Failed to start Gemini: {e}"
        logger.warning(error)
        return GeminiResult(response_text="", session_id=None, error=error)

    # Read response
    deadline = time.time() + timeout_seconds
    response_text, new_session_id, error, usage = _read_stream_response(
        proc,
        deadline=deadline,
        log_path=log_path,
    )

    # Wait for process to finish
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    # Update session ID if we got one
    if new_session_id:
        gemini_session.session_id = new_session_id

    if error:
        logger.warning("Gemini error: %s", error)

    duration_ms = usage.get("duration_ms") if usage else None

    return GeminiResult(
        response_text=response_text,
        session_id=gemini_session.session_id,
        usage=usage,
        duration_ms=duration_ms,
        error=error,
    )
