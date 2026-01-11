"""Gemini CLI client for Wintermute agent sessions.

This implementation uses a persistent subprocess with streaming JSON I/O,
similar to the Claude client pattern. The Gemini process stays alive
and prompts are sent via stdin with responses streamed via stdout.

Launch: gemini --output-format stream-json
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
class GeminiProcess:
    """Manages a persistent Gemini subprocess."""

    proc: subprocess.Popen
    session_id: Optional[str] = None
    last_used: float = 0.0


_GEMINI_LOCK = threading.Lock()
_GEMINI_PROCESSES: dict[str, GeminiProcess] = {}


def _is_local_host(host: str) -> bool:
    """Check if host refers to the local machine."""
    value = host.strip().lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    hostname = socket.gethostname().lower()
    fqdn = socket.getfqdn().lower()
    return value in {hostname, fqdn, f"{hostname}.local", f"{fqdn}.local"}


def _build_gemini_shell(agent: AgentRecord, cwd: str) -> str:
    """Build the shell command to launch Gemini in streaming mode."""
    cmd = agent.command.strip() if agent.command else "gemini"

    # Core flags for streaming JSON mode
    # Gemini uses positional prompts and --output-format for JSON streaming
    # Use -i for interactive mode so it stays alive for multiple prompts
    args = [cmd, "--output-format", "stream-json"]

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
    # cd to cwd first since Gemini doesn't have a --cwd flag
    wrapped = (
        "source ~/.profile >/dev/null 2>&1; "
        "source ~/.bash_profile >/dev/null 2>&1; "
        "source ~/.bashrc >/dev/null 2>&1; "
        f"cd {shlex.quote(cwd)}; "
        "if command -v stdbuf >/dev/null 2>&1; then "
        f"exec stdbuf -oL -eL {cmd_str}; "
        "else "
        f"exec {cmd_str}; "
        "fi"
    )
    return wrapped


def _start_gemini_process(spec: SSHSpec, agent: AgentRecord, cwd: str) -> subprocess.Popen:
    """Start a persistent Gemini subprocess."""
    shell_cmd = _build_gemini_shell(agent, cwd)
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
        return subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
        )

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
    return subprocess.Popen(
        ssh_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )


def _send_user_message(proc: subprocess.Popen, text: str) -> None:
    """Send a user message to Gemini via stdin.

    Gemini CLI in interactive mode reads prompts from stdin as plain text lines.
    """
    if not proc.stdin:
        raise RuntimeError("Gemini process stdin closed")

    # Gemini reads plain text prompts from stdin, one per line
    data = (text + "\n").encode("utf-8")
    proc.stdin.write(data)
    proc.stdin.flush()


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
    idle_seconds: float = 2.0,
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
    last_activity = time.time()
    result_received = False

    while time.time() < deadline:
        remaining = max(deadline - time.time(), 0.1)
        events = selector.select(timeout=remaining)

        if not events:
            # If we got a result message and idle, we're done
            if result_received and (time.time() - last_activity) >= idle_seconds:
                break
            # If we have texts and idle, we're done
            if texts and (time.time() - last_activity) >= idle_seconds:
                break
            continue

        for key, _mask in events:
            is_stderr = proc.stderr is not None and key.fileobj is proc.stderr
            chunk = os.read(key.fileobj.fileno(), 4096)

            if not chunk:
                selector.close()
                return "\n".join(texts), session_id, error, usage

            text = chunk.decode("utf-8", errors="ignore")

            if is_stderr:
                if log_path:
                    _append_gemini_log(log_path, f"[stderr] {text.strip()}")
                # Gemini outputs "Loaded cached credentials." to stderr, ignore it
                if "credentials" not in text.lower():
                    logger.warning("Gemini stderr: %s", text.strip())
                last_activity = time.time()
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

                last_activity = time.time()
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
                            if payload.get("delta"):
                                texts.append(content)
                            elif content not in texts:
                                texts.append(content)

                elif msg_type == "result":
                    result_received = True
                    usage = payload.get("stats")
                    status = payload.get("status")
                    if status != "success":
                        error = payload.get("error") or f"Gemini result status: {status}"
                    # Result message means the response is complete
                    break

    selector.close()
    # Join all delta texts into a single response
    return "".join(texts), session_id, error, usage


def get_gemini_process(
    wintermute_session_id: str,
    spec: SSHSpec,
    agent: AgentRecord,
    cwd: str,
) -> GeminiProcess:
    """Get or create a persistent Gemini process for a session."""
    with _GEMINI_LOCK:
        existing = _GEMINI_PROCESSES.get(wintermute_session_id)
        if existing and existing.proc.poll() is None:
            return existing
        if existing:
            _GEMINI_PROCESSES.pop(wintermute_session_id, None)

        proc = _start_gemini_process(spec, agent, cwd)
        gemini = GeminiProcess(proc=proc, session_id=None, last_used=time.time())
        _GEMINI_PROCESSES[wintermute_session_id] = gemini

    return gemini


def close_gemini_process(wintermute_session_id: str) -> None:
    """Close and cleanup a Gemini process."""
    with _GEMINI_LOCK:
        gemini = _GEMINI_PROCESSES.pop(wintermute_session_id, None)

    if not gemini:
        return

    try:
        if gemini.proc.stdin:
            gemini.proc.stdin.close()
    except Exception:
        pass

    try:
        gemini.proc.terminate()
    except Exception:
        pass

    try:
        gemini.proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        gemini.proc.kill()


def poll_gemini(wintermute_session_id: str, timeout_seconds: int = 5) -> GeminiResult:
    """Poll for any pending output from Gemini process."""
    logger = logging.getLogger(__name__)

    with _GEMINI_LOCK:
        gemini = _GEMINI_PROCESSES.get(wintermute_session_id)

    if not gemini:
        return GeminiResult(
            response_text="",
            session_id=None,
            error="Gemini process not found",
        )

    if gemini.proc.poll() is not None:
        close_gemini_process(wintermute_session_id)
        return GeminiResult(
            response_text="",
            session_id=None,
            error="Gemini process exited",
        )

    log_path = _get_gemini_log_path(wintermute_session_id)
    deadline = time.time() + timeout_seconds

    response_text, session_id, error, usage = _read_stream_response(
        gemini.proc,
        deadline=deadline,
        idle_seconds=0.5,
        log_path=log_path,
    )

    return GeminiResult(
        response_text=response_text,
        session_id=session_id or gemini.session_id,
        usage=usage,
        error=error,
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

    Uses a persistent subprocess - the process stays alive between calls.
    Session context is maintained automatically by Gemini.

    Args:
        spec: SSH connection specification for the target VM
        agent: Agent configuration record
        session_id: Wintermute session ID (for process management)
        prompt: The prompt to send to Gemini
        cwd: Working directory for the Gemini session
        timeout_seconds: Response timeout in seconds

    Returns:
        GeminiResult with response text and session info
    """
    logger = logging.getLogger(__name__)
    log_path = _get_gemini_log_path(session_id)

    # Get or create persistent process
    gemini = get_gemini_process(session_id, spec, agent, cwd)

    if gemini.proc.poll() is not None:
        error = "Gemini process exited unexpectedly"
        logger.warning(error)
        close_gemini_process(session_id)
        return GeminiResult(response_text="", session_id=None, error=error)

    gemini.last_used = time.time()

    # Send the prompt
    _append_gemini_log(log_path, f"[prompt] {prompt}")
    try:
        _send_user_message(gemini.proc, prompt)
    except Exception as e:
        error = f"Failed to send prompt: {e}"
        logger.warning(error)
        close_gemini_process(session_id)
        return GeminiResult(response_text="", session_id=gemini.session_id, error=error)

    # Read response
    deadline = time.time() + timeout_seconds
    response_text, new_session_id, error, usage = _read_stream_response(
        gemini.proc,
        deadline=deadline,
        idle_seconds=2.0,
        log_path=log_path,
    )

    # Update session ID if we got one
    if new_session_id:
        gemini.session_id = new_session_id

    if error:
        logger.warning("Gemini error: %s", error)

    if gemini.proc.poll() is not None:
        logger.warning("Gemini process exited after response")
        close_gemini_process(session_id)

    duration_ms = usage.get("duration_ms") if usage else None

    return GeminiResult(
        response_text=response_text,
        session_id=gemini.session_id,
        usage=usage,
        duration_ms=duration_ms,
        error=error,
    )
