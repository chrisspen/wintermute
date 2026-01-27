"""Claude Code CLI client for Wintermute agent sessions.

This implementation uses a persistent subprocess with streaming JSON I/O,
similar to the Codex MCP client pattern. The Claude process stays alive
and prompts are sent via stdin with responses streamed via stdout.

Launch: claude -p --input-format stream-json --output-format stream-json
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
class ClaudeResult:
    """Result from a Claude CLI invocation."""

    response_text: str
    session_id: Optional[str]
    usage: Optional[dict[str, Any]] = None
    total_cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    error: Optional[str] = None
    had_activity: bool = False # True if any stream data was received (even without response_text)


@dataclass
class ClaudeProcess:
    """Manages a persistent Claude subprocess."""

    proc: subprocess.Popen
    session_id: Optional[str] = None
    last_used: float = 0.0


_CLAUDE_LOCK = threading.Lock()
_CLAUDE_PROCESSES: dict[str, ClaudeProcess] = {}


def _is_local_host(host: str) -> bool:
    """Check if host refers to the local machine."""
    value = host.strip().lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    hostname = socket.gethostname().lower()
    fqdn = socket.getfqdn().lower()
    return value in {hostname, fqdn, f"{hostname}.local", f"{fqdn}.local"}


def _build_claude_shell(agent: AgentRecord, cwd: str) -> str:
    """Build the shell command to launch Claude in streaming mode."""
    cmd = agent.command.strip() if agent.command else "claude"

    # Core flags for persistent streaming mode
    # Note: cwd is handled by cd'ing to the directory before running claude
    # --verbose is required when using --output-format=stream-json with -p
    # --dangerously-skip-permissions allows full access within the repo
    args = [
        cmd,
        "-p",
        "--verbose",
        "--dangerously-skip-permissions",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
    ]

    # Parse additional config from agent.mcp_config
    if agent.mcp_config:
        config = agent.mcp_config.strip()
        if config:
            try:
                extra_args = shlex.split(config)
                args.extend(extra_args)
            except ValueError:
                args.extend(config.split())

    cmd_str = shlex.join(args)
    if agent.env_vars:
        env_vars = agent.env_vars.strip()
        if env_vars:
            cmd_str = f"env {env_vars} {cmd_str}"

    # Wrap with profile sourcing and line buffering
    # Source nvm directly since .bashrc exits early for non-interactive shells
    wrapped = (
        "source ~/.profile >/dev/null 2>&1; "
        "source ~/.bash_profile >/dev/null 2>&1; "
        "export NVM_DIR=\"$HOME/.nvm\"; "
        "[ -s \"$NVM_DIR/nvm.sh\" ] && . \"$NVM_DIR/nvm.sh\"; "
        "if command -v stdbuf >/dev/null 2>&1; then "
        f"exec stdbuf -oL -eL {cmd_str}; "
        "else "
        f"exec {cmd_str}; "
        "fi"
    )
    return wrapped


def _start_claude_process(spec: SSHSpec, agent: AgentRecord, cwd: str) -> subprocess.Popen:
    """Start a persistent Claude subprocess."""
    shell_cmd = _build_claude_shell(agent, cwd)
    logger = logging.getLogger(__name__)
    local_host = _is_local_host(spec.host)
    current_user = getpass.getuser()

    if local_host and spec.user == current_user:
        logger.info(
            "Claude local mode host=%s user=%s cwd=%s",
            spec.host,
            spec.user,
            cwd,
        )
        # cd to cwd first since we removed --cwd flag
        full_cmd = f"cd {shlex.quote(cwd)} && {shell_cmd}"
        logger.info("Claude local command: %s", full_cmd)
        return subprocess.Popen(
            ["bash", "-lc", full_cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
        )

    logger.info(
        "Claude SSH mode host=%s user=%s current_user=%s cwd=%s",
        spec.host,
        spec.user,
        current_user,
        cwd,
    )
    ssh_cmd = [
        "ssh",
        "-T",
        "-o",
        "RequestTTY=no",
        "-o",
        "BatchMode=yes",
        "-o",
        "ConnectTimeout=10",
        "-p",
        str(spec.port),
        *spec.options,
        f"{spec.user}@{spec.host}",
        f"cd {shlex.quote(cwd)} && bash -lc {shlex.quote(shell_cmd)}",
    ]
    logger.info("Claude SSH command: %s", " ".join(ssh_cmd))
    return subprocess.Popen(
        ssh_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )


def _send_user_message(proc: subprocess.Popen, text: str) -> None:
    """Send a user message to Claude via stdin."""
    if not proc.stdin:
        raise RuntimeError("Claude process stdin closed")

    # Format as a user message in stream-json format
    message = {
        "type": "user",
        "message": {
            "role": "user",
            "content": text,
        },
    }
    data = (json.dumps(message) + "\n").encode("utf-8")
    proc.stdin.write(data)
    proc.stdin.flush()


def _get_claude_log_path(session_id: str) -> str:
    """Get path for Claude session log file."""
    base_dir = os.path.join(os.getcwd(), ".runtime", "logs")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"claude_{session_id}.log")


def _append_claude_log(log_path: str, entry: str) -> None:
    """Append an entry to the Claude session log."""
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(entry + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Failed to write Claude log entry")


def _read_stream_response(
    proc: subprocess.Popen,
    *,
    deadline: float,
    idle_seconds: float = 2.0,
    log_path: Optional[str] = None,
) -> tuple[str, Optional[str], Optional[str], Optional[dict[str, Any]], bool]:
    """Read streaming JSON response from Claude.

    Returns: (response_text, session_id, error, usage, had_activity)
    The had_activity flag is True if any data was received from the stream,
    even if there's no final response_text (e.g., Claude is running tools).
    """
    logger = logging.getLogger(__name__)
    if not proc.stdout:
        return "", None, "Claude process stdout closed", None, False

    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    if proc.stderr:
        selector.register(proc.stderr, selectors.EVENT_READ)

    buffer = ""
    texts: list[str] = []
    session_id: Optional[str] = None
    error: Optional[str] = None
    usage: Optional[dict[str, Any]] = None
    last_activity = time.time()
    result_received = False
    had_activity = False

    done = False
    while time.time() < deadline and not done:
        remaining = max(deadline - time.time(), 0.1)
        events = selector.select(timeout=remaining)

        if not events:
            # Only consider response complete if we got the result message
            # and have been idle for a while (in case of late output)
            if result_received and (time.time() - last_activity) >= idle_seconds:
                break
            continue

        for key, _mask in events:
            is_stderr = proc.stderr is not None and key.fileobj is proc.stderr
            chunk = os.read(key.fileobj.fileno(), 4096)

            if not chunk:
                selector.close()
                return "\n".join(texts), session_id, error, usage, had_activity

            had_activity = True
            text = chunk.decode("utf-8", errors="ignore")

            if is_stderr:
                if log_path:
                    _append_claude_log(log_path, f"[stderr] {text.strip()}")
                logger.warning("Claude stderr: %s", text.strip())
                last_activity = time.time()
                continue

            buffer += text

            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue

                if log_path:
                    _append_claude_log(log_path, line)

                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("Claude non-JSON output: %s", line)
                    continue

                last_activity = time.time()
                msg_type = payload.get("type")

                if msg_type == "system" and payload.get("subtype") == "init":
                    session_id = payload.get("session_id")
                    logger.info("Claude session initialized: %s", session_id)

                elif msg_type == "assistant":
                    content = payload.get("message", {}).get("content", [])
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            block_text = block.get("text", "")
                            if block_text and block_text not in texts:
                                texts.append(block_text)

                elif msg_type == "result":
                    result_received = True
                    session_id = payload.get("session_id") or session_id
                    usage = payload.get("usage")
                    if payload.get("is_error"):
                        error = payload.get("result") or "Unknown error"
                    elif payload.get("result"):
                        result_text = payload.get("result")
                        if result_text and result_text not in texts:
                            texts.append(result_text)
                    # Result message means the response is complete
                    done = True
                    break

    selector.close()
    return "\n".join(texts), session_id, error, usage, had_activity


def get_claude_process(
    wintermute_session_id: str,
    spec: SSHSpec,
    agent: AgentRecord,
    cwd: str,
) -> ClaudeProcess:
    """Get or create a persistent Claude process for a session."""
    with _CLAUDE_LOCK:
        existing = _CLAUDE_PROCESSES.get(wintermute_session_id)
        if existing and existing.proc.poll() is None:
            return existing
        if existing:
            _CLAUDE_PROCESSES.pop(wintermute_session_id, None)

        proc = _start_claude_process(spec, agent, cwd)
        claude = ClaudeProcess(proc=proc, session_id=None, last_used=time.time())
        _CLAUDE_PROCESSES[wintermute_session_id] = claude

    return claude


def close_claude_process(wintermute_session_id: str) -> None:
    """Close and cleanup a Claude process."""
    with _CLAUDE_LOCK:
        claude = _CLAUDE_PROCESSES.pop(wintermute_session_id, None)

    if not claude:
        return

    try:
        if claude.proc.stdin:
            claude.proc.stdin.close()
    except Exception:
        pass

    try:
        claude.proc.terminate()
    except Exception:
        pass

    try:
        claude.proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        claude.proc.kill()


def poll_claude(wintermute_session_id: str, timeout_seconds: int = 5) -> ClaudeResult:
    """Poll for any pending output from Claude process."""
    logger = logging.getLogger(__name__)

    with _CLAUDE_LOCK:
        claude = _CLAUDE_PROCESSES.get(wintermute_session_id)

    if not claude:
        return ClaudeResult(
            response_text="",
            session_id=None,
            error="Claude process not found",
        )

    if claude.proc.poll() is not None:
        close_claude_process(wintermute_session_id)
        return ClaudeResult(
            response_text="",
            session_id=None,
            error="Claude process exited",
        )

    log_path = _get_claude_log_path(wintermute_session_id)
    deadline = time.time() + timeout_seconds

    response_text, session_id, error, usage, had_activity = _read_stream_response(
        claude.proc,
        deadline=deadline,
        idle_seconds=0.5,
        log_path=log_path,
    )

    return ClaudeResult(
        response_text=response_text,
        session_id=session_id or claude.session_id,
        usage=usage,
        error=error,
        had_activity=had_activity,
    )


def run_claude_prompt(
    spec: SSHSpec,
    agent: AgentRecord,
    *,
    session_id: str,
    prompt: str,
    cwd: str,
    timeout_seconds: int = 300,
) -> ClaudeResult:
    """Send a prompt to Claude and wait for response.

    Uses a persistent subprocess - the process stays alive between calls.
    Session context is maintained automatically by Claude.

    Args:
        spec: SSH connection specification for the target VM
        agent: Agent configuration record
        session_id: Wintermute session ID (for process management)
        prompt: The prompt to send to Claude
        cwd: Working directory for the Claude session
        timeout_seconds: Response timeout in seconds

    Returns:
        ClaudeResult with response text and session info
    """
    logger = logging.getLogger(__name__)
    log_path = _get_claude_log_path(session_id)

    # Get or create persistent process
    claude = get_claude_process(session_id, spec, agent, cwd)

    if claude.proc.poll() is not None:
        error = "Claude process exited unexpectedly"
        logger.warning(error)
        close_claude_process(session_id)
        return ClaudeResult(response_text="", session_id=None, error=error)

    claude.last_used = time.time()

    # Send the prompt
    _append_claude_log(log_path, f"[prompt] {prompt}")
    try:
        _send_user_message(claude.proc, prompt)
    except Exception as e:
        error = f"Failed to send prompt: {e}"
        logger.warning(error)
        close_claude_process(session_id)
        return ClaudeResult(response_text="", session_id=claude.session_id, error=error)

    # Read response
    deadline = time.time() + timeout_seconds
    response_text, new_session_id, error, usage, had_activity = _read_stream_response(
        claude.proc,
        deadline=deadline,
        idle_seconds=2.0,
        log_path=log_path,
    )

    # Update session ID if we got one
    if new_session_id:
        claude.session_id = new_session_id

    if error:
        logger.warning("Claude error: %s", error)

    if claude.proc.poll() is not None:
        logger.warning("Claude process exited after response")
        close_claude_process(session_id)

    return ClaudeResult(
        response_text=response_text,
        session_id=claude.session_id,
        usage=usage,
        error=error,
        had_activity=had_activity,
    )
