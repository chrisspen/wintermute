"""Minimal MCP client for Codex via SSH stdio."""

from __future__ import annotations

import getpass
import json
import logging
import re
import shlex
import socket
import subprocess
import time
import os
import selectors
import threading
from dataclasses import dataclass
from typing import Any, Optional

try:
    import tomllib as toml
except ImportError: # pragma: no cover - py<3.11
    import tomli as toml

from wintermute.db import AgentRecord
from wintermute.runner import SSHSpec


@dataclass(frozen=True)
class MCPResult:
    response_text: str
    conversation_id: Optional[str]
    error: Optional[str] = None


@dataclass
class MCPProcess:
    proc: subprocess.Popen
    next_id: int = 1
    initialized: bool = False
    tools: list[dict[str, Any]] = None
    last_used: float = 0.0


_MCP_LOCK = threading.Lock()
_MCP_PROCESSES: dict[str, MCPProcess] = {}


def _build_mcp_shell(agent: AgentRecord) -> str:
    cmd = agent.command.strip()
    if agent.env_vars:
        env_vars = agent.env_vars.strip()
        if env_vars:
            cmd = f"env {env_vars} {cmd}"
    cmd = f"{cmd} mcp-server"
    if agent.mcp_config:
        config = agent.mcp_config.strip()
        if config:
            cmd = f"{cmd} {config}"
            if "network-full-access" in config and "network_access" not in config:
                cmd = f"{cmd} -c 'network_access=\"enabled\"'"
    wrapped = (
        "source ~/.profile >/dev/null 2>&1; "
        "source ~/.bash_profile >/dev/null 2>&1; "
        "source ~/.bashrc >/dev/null 2>&1; "
        "if command -v stdbuf >/dev/null 2>&1; then "
        f"exec stdbuf -oL -eL {cmd}; "
        "else "
        f"exec {cmd}; "
        "fi"
    )
    return wrapped


def _parse_toml_value(raw: str) -> Any:
    try:
        parsed = toml.loads(f"value = {raw}")
        return parsed.get("value")
    except Exception:
        return raw


def _set_nested_config(overrides: dict[str, Any], key: str, value: Any) -> None:
    parts = [part for part in key.split(".") if part]
    current = overrides
    for part in parts[:-1]:
        next_node = current.get(part)
        if not isinstance(next_node, dict):
            next_node = {}
            current[part] = next_node
        current = next_node
    if parts:
        current[parts[-1]] = value


def _parse_mcp_config_overrides(config: Optional[str]) -> dict[str, Any]:
    if not config:
        return {}
    overrides: dict[str, Any] = {}
    try:
        tokens = shlex.split(config)
    except ValueError:
        tokens = config.split()
    idx = 0
    while idx < len(tokens):
        token = tokens[idx]
        if token in {"-c", "--config"} and idx + 1 < len(tokens):
            key_value = tokens[idx + 1]
            if "=" in key_value:
                key, raw_value = key_value.split("=", 1)
                value = _parse_toml_value(raw_value)
                _set_nested_config(overrides, key, value)
            idx += 2
            continue
        idx += 1
    return overrides


def _is_local_host(host: str) -> bool:
    value = host.strip().lower()
    if value in {"localhost", "127.0.0.1", "::1"}:
        return True
    hostname = socket.gethostname().lower()
    fqdn = socket.getfqdn().lower()
    return value in {hostname, fqdn, f"{hostname}.local", f"{fqdn}.local"}


def _start_mcp_process(spec: SSHSpec, agent: AgentRecord) -> subprocess.Popen:
    shell_cmd = _build_mcp_shell(agent)
    logger = logging.getLogger(__name__)
    local_host = _is_local_host(spec.host)
    current_user = getpass.getuser()
    if local_host and spec.user == current_user:
        logger.info(
            "MCP local mode host=%s user=%s hostname=%s fqdn=%s",
            spec.host,
            spec.user,
            socket.gethostname(),
            socket.getfqdn(),
        )
        logging.getLogger(__name__).info("MCP local command: %s", shell_cmd)
        return subprocess.Popen(
            ["bash", "-lc", shell_cmd],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
        )
    logger.info(
        "MCP SSH mode host=%s user=%s current_user=%s hostname=%s fqdn=%s",
        spec.host,
        spec.user,
        current_user,
        socket.gethostname(),
        socket.getfqdn(),
    )
    cmd = [
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
        f"bash -lc {shlex.quote(shell_cmd)}",
    ]
    logging.getLogger(__name__).info("MCP SSH command: %s", " ".join(cmd))
    return subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=False,
        bufsize=0,
    )


def _send_json(proc: subprocess.Popen, payload: dict[str, Any]) -> None:
    if not proc.stdin:
        raise RuntimeError("MCP process stdin closed")
    data = (json.dumps(payload) + "\n").encode("utf-8")
    proc.stdin.write(data)
    proc.stdin.flush()


def _next_id(mcp: MCPProcess) -> int:
    value = mcp.next_id
    mcp.next_id += 1
    return value


def _read_messages_until(
    proc: subprocess.Popen,
    *,
    deadline: float,
    target_id: Optional[int] = None,
    idle_seconds: float = 2.0,
) -> list[dict[str, Any]]:
    logger = logging.getLogger(__name__)
    messages: list[dict[str, Any]] = []
    if not proc.stdout:
        return messages
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    if proc.stderr:
        selector.register(proc.stderr, selectors.EVENT_READ)
    buffer = ""
    last_activity = time.time()
    found_target = False
    while time.time() < deadline:
        remaining = max(deadline - time.time(), 0.1)
        events = selector.select(timeout=remaining)
        if not events:
            if found_target and (time.time() - last_activity) >= idle_seconds:
                break
            if buffer and (time.time() - last_activity) >= idle_seconds:
                logger.warning("MCP non-JSON buffer: %s", buffer.strip())
                buffer = ""
            continue
        for key, _mask in events:
            is_stderr = proc.stderr is not None and key.fileobj is proc.stderr
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
                return messages
            text = chunk.decode("utf-8", errors="ignore")
            if is_stderr:
                logger.warning("MCP stderr: %s", text.strip())
                last_activity = time.time()
                continue
            buffer += text
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("MCP non-JSON output: %s", line)
                    continue
                messages.append(payload)
                last_activity = time.time()
                if target_id is not None and payload.get("id") == target_id:
                    if "result" in payload or "error" in payload:
                        found_target = True
    return messages


def _collect_response(messages: list[dict[str, Any]]) -> tuple[str, Optional[str], Optional[str]]:
    response_texts: list[str] = []
    conversation_id: Optional[str] = None
    error: Optional[str] = None
    for payload in messages:
        if payload.get("error"):
            error = json.dumps(payload.get("error"))
            continue
        result = payload.get("result")
        if isinstance(result, dict):
            conv = result.get("conversationId") or result.get("conversation_id")
            if conv:
                conversation_id = str(conv)
            if isinstance(result.get("text"), str):
                response_texts.append(str(result["text"]))
            if isinstance(result.get("output_text"), str):
                response_texts.append(str(result["output_text"]))
            if isinstance(result.get("output"), str):
                response_texts.append(str(result["output"]))
            message = result.get("message")
            if isinstance(message, dict):
                message_content = message.get("content")
                if isinstance(message_content, list):
                    for item in message_content:
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("value")
                            if isinstance(text, str) and text.strip():
                                response_texts.append(text)
                elif isinstance(message.get("text"), str):
                    response_texts.append(str(message["text"]))
            content = result.get("content")
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("value")
                        if isinstance(text, str) and text.strip():
                            response_texts.append(text)
            elif isinstance(content, str):
                response_texts.append(content)
        if payload.get("method") == "codex/event":
            params = payload.get("params") or {}
            if isinstance(params, dict):
                conv = params.get("session_id") or params.get("sessionId")
                # session_id can also be inside params.msg (session_configured event)
                if not conv:
                    msg_dict = params.get("msg")
                    if isinstance(msg_dict, dict):
                        conv = msg_dict.get("session_id") or msg_dict.get("sessionId")
                if conv:
                    conversation_id = str(conv)
                text = params.get("message") or params.get("content") or params.get("text") or ""
                if isinstance(text, str) and text.strip():
                    response_texts.append(text)
                content = params.get("content")
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text") or item.get("value")
                            if isinstance(text, str) and text.strip():
                                response_texts.append(text)
                msg = params.get("msg")
                if isinstance(msg, dict):
                    if isinstance(msg.get("text"), str):
                        response_texts.append(str(msg["text"]))
                    msg_content = msg.get("content")
                    if isinstance(msg_content, list):
                        for item in msg_content:
                            if isinstance(item, dict):
                                text = item.get("text") or item.get("value")
                                if isinstance(text, str) and text.strip():
                                    response_texts.append(text)
                    if msg.get("type") == "raw_response_item":
                        item = msg.get("item") or {}
                        if isinstance(item, dict) and item.get("type") == "message":
                            if item.get("role") == "assistant":
                                for block in item.get("content") or []:
                                    if isinstance(block, dict):
                                        text = block.get("text") or block.get("value")
                                        if isinstance(text, str) and text.strip():
                                            response_texts.append(text)
    response_text = "\n".join(text for text in response_texts if text)
    return response_text, conversation_id, error


def _extract_assistant_text(payload: dict[str, Any]) -> tuple[list[str], Optional[str], Optional[str]]:
    if payload.get("error"):
        return [], None, json.dumps(payload.get("error"))
    if payload.get("method") != "codex/event":
        return [], None, None
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return [], None, None
    # session_id can be at params level or inside params.msg (session_configured event)
    conv = params.get("session_id") or params.get("sessionId")
    msg = params.get("msg")
    if not conv and isinstance(msg, dict):
        conv = msg.get("session_id") or msg.get("sessionId")
    conversation_id = str(conv) if conv else None
    if not isinstance(msg, dict):
        return [], conversation_id, None
    msg_type = msg.get("type")
    texts: list[str] = []
    if msg_type == "raw_response_item":
        item = msg.get("item") or {}
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role") == "assistant":
            contents = item.get("content") or []
            for block in contents:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("value")
                    if isinstance(text, str) and text.strip():
                        texts.append(text)
            return texts, conversation_id, None
        return [], conversation_id, None
    if msg.get("role") == "assistant" or msg_type in {
        "assistant_message",
        "assistant_response",
        "assistant_output",
        "assistant",
    }:
        content = msg.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("value")
                    if isinstance(text, str) and text.strip():
                        texts.append(text)
        elif isinstance(content, str) and content.strip():
            texts.append(content)
        text_value = msg.get("text")
        if isinstance(text_value, str) and text_value.strip():
            texts.append(text_value)
    return texts, conversation_id, None


def _get_mcp_log_path(session_id: str) -> str:
    base_dir = os.path.join(os.getcwd(), ".runtime", "logs")
    os.makedirs(base_dir, exist_ok=True)
    return os.path.join(base_dir, f"mcp_{session_id}.log")


def _append_mcp_log(log_path: str, line: str) -> None:
    try:
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        logging.getLogger(__name__).warning("Failed to write MCP log line")


def _read_stream_response(
    proc: subprocess.Popen,
    *,
    deadline: float,
    idle_seconds: float = 0.5,
    log_path: Optional[str] = None,
) -> tuple[str, Optional[str], Optional[str], list[dict[str, Any]]]:
    logger = logging.getLogger(__name__)
    if not proc.stdout:
        return "", None, "MCP process stdout closed", []
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    if proc.stderr:
        selector.register(proc.stderr, selectors.EVENT_READ)
    buffer = ""
    texts: list[str] = []
    conversation_id: Optional[str] = None
    error: Optional[str] = None
    messages: list[dict[str, Any]] = []
    last_activity = time.time()
    while time.time() < deadline:
        remaining = max(deadline - time.time(), 0.1)
        events = selector.select(timeout=remaining)
        if not events:
            if texts and (time.time() - last_activity) >= idle_seconds:
                break
            if buffer and (time.time() - last_activity) >= idle_seconds:
                logger.warning("MCP non-JSON buffer: %s", buffer.strip())
                buffer = ""
            continue
        for key, _mask in events:
            is_stderr = proc.stderr is not None and key.fileobj is proc.stderr
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
                return "\n".join(texts), conversation_id, error, messages
            text = chunk.decode("utf-8", errors="ignore")
            if is_stderr:
                if log_path:
                    _append_mcp_log(log_path, f"[stderr] {text.strip()}")
                logger.warning("MCP stderr: %s", text.strip())
                last_activity = time.time()
                continue
            buffer += text
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                if log_path:
                    _append_mcp_log(log_path, line)
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("MCP non-JSON output: %s", line)
                    continue
                messages.append(payload)
                last_activity = time.time()
                extracted, conv, payload_error = _extract_assistant_text(payload)
                if conv and not conversation_id:
                    conversation_id = conv
                if payload_error and not error:
                    error = payload_error
                if extracted:
                    for chunk_text in extracted:
                        if chunk_text not in texts:
                            texts.append(chunk_text)
    return "\n".join(texts), conversation_id, error, messages


def _ensure_initialized(mcp: MCPProcess) -> Optional[str]:
    logger = logging.getLogger(__name__)
    if mcp.proc.poll() is not None:
        return "MCP process exited before initialization"
    init_id = _next_id(mcp)
    _send_json(
        mcp.proc,
        {
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "wintermute",
                    "version": "0.1.0"
                },
            },
        },
    )
    deadline = time.time() + 10
    init_messages = _read_messages_until(mcp.proc, deadline=deadline, target_id=init_id, idle_seconds=1.0)
    if not any(payload.get("id") == init_id for payload in init_messages):
        logger.warning("MCP initialize timed out (messages=%s)", len(init_messages))
        return "MCP initialize timed out"
    _send_json(mcp.proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    tools_id = _next_id(mcp)
    _send_json(
        mcp.proc,
        {
            "jsonrpc": "2.0",
            "id": tools_id,
            "method": "tools/list",
            "params": {},
        },
    )
    tool_messages = _read_messages_until(mcp.proc, deadline=deadline, target_id=tools_id, idle_seconds=1.0)
    tools: list[dict[str, Any]] = []
    for payload in tool_messages:
        if payload.get("id") == tools_id and isinstance(payload.get("result"), dict):
            tools = payload["result"].get("tools") or []
            break
    if not tools:
        logger.warning("MCP tool list response missing or empty")
    else:
        logger.info("MCP tools available: %s", [t.get("name") for t in tools if isinstance(t, dict)])
    mcp.tools = tools
    mcp.initialized = True
    return None


def _pick_tool_name(mcp: MCPProcess, conversation_id: Optional[str]) -> str:
    tools = mcp.tools or []
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    if conversation_id and "codex-reply" in names:
        return "codex-reply"
    return "codex"


def _send_tool_call(
    mcp: MCPProcess,
    *,
    prompt: str,
    cwd: str,
    conversation_id: Optional[str],
    stream_timeout_seconds: int,
    network_access_enabled: bool,
    config_overrides: Optional[dict[str, Any]],
    sandbox_mode: str,
    log_path: Optional[str] = None,
) -> MCPResult:
    logger = logging.getLogger(__name__)
    if mcp.proc.poll() is not None:
        return MCPResult(response_text="", conversation_id=conversation_id, error="MCP process exited")
    tool = _pick_tool_name(mcp, conversation_id)
    call_id = _next_id(mcp)
    arguments: dict[str, Any] = {
        "prompt": prompt,
        "cwd": cwd,
        "approval-policy": "never",
        "sandbox": sandbox_mode,
    }
    if network_access_enabled:
        arguments["network_access"] = "enabled"
    if config_overrides:
        arguments["config"] = config_overrides
    if conversation_id:
        arguments["conversationId"] = conversation_id
    _send_json(
        mcp.proc,
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": tool,
                "arguments": arguments
            },
        },
    )
    deadline = time.time() + stream_timeout_seconds
    response_text, conv_id, error, tool_messages = _read_stream_response(
        mcp.proc,
        deadline=deadline,
        idle_seconds=0.5,
        log_path=log_path,
    )
    if not conv_id:
        conv_id = conversation_id
    if error:
        logger.warning("MCP error: %s", error)
    if not response_text:
        preview = []
        for payload in tool_messages[:5]:
            preview.append(json.dumps(payload)[:400])
        logger.info(
            "MCP received no agent response within %ss (messages=%s preview=%s)",
            stream_timeout_seconds,
            len(tool_messages),
            preview,
        )
    return MCPResult(response_text=response_text, conversation_id=conv_id, error=error)


def get_mcp_process(session_id: str, spec: SSHSpec, agent: AgentRecord) -> MCPProcess:
    with _MCP_LOCK:
        existing = _MCP_PROCESSES.get(session_id)
        if existing and existing.proc.poll() is None:
            return existing
        if existing:
            _MCP_PROCESSES.pop(session_id, None)
        proc = _start_mcp_process(spec, agent)
        mcp = MCPProcess(proc=proc, next_id=1, initialized=False, tools=None, last_used=time.time())
        _MCP_PROCESSES[session_id] = mcp
    error = _ensure_initialized(mcp)
    if error:
        return mcp
    return mcp


def close_mcp_process(session_id: str) -> None:
    with _MCP_LOCK:
        mcp = _MCP_PROCESSES.pop(session_id, None)
    if not mcp:
        return
    try:
        if mcp.proc.stdin:
            mcp.proc.stdin.close()
    except Exception:
        pass
    try:
        mcp.proc.terminate()
    except Exception:
        pass
    try:
        mcp.proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        mcp.proc.kill()


def poll_codex_mcp(session_id: str, timeout_seconds: int = 5) -> MCPResult:
    logger = logging.getLogger(__name__)
    with _MCP_LOCK:
        mcp = _MCP_PROCESSES.get(session_id)
    if not mcp:
        return MCPResult(response_text="", conversation_id=None, error="MCP process not found")
    if mcp.proc.poll() is not None:
        close_mcp_process(session_id)
        return MCPResult(response_text="", conversation_id=None, error="MCP process exited")
    deadline = time.time() + timeout_seconds
    log_path = _get_mcp_log_path(session_id)
    response_text, conv_id, error, messages = _read_stream_response(
        mcp.proc,
        deadline=deadline,
        idle_seconds=0.5,
        log_path=log_path,
    )
    if not response_text and messages:
        logger.info("MCP poll returned no assistant output (messages=%s)", len(messages))
    return MCPResult(response_text=response_text, conversation_id=conv_id, error=error)


def run_codex_mcp(
    spec: SSHSpec,
    agent: AgentRecord,
    *,
    session_id: str,
    prompt: str,
    cwd: str,
    conversation_id: Optional[str],
    timeout_seconds: int = 5,
) -> MCPResult:
    logger = logging.getLogger(__name__)
    mcp = get_mcp_process(session_id, spec, agent)
    if not mcp.initialized:
        error = "MCP process failed to initialize"
        logger.warning(error)
        close_mcp_process(session_id)
        return MCPResult(response_text="", conversation_id=conversation_id, error=error)
    mcp.last_used = time.time()
    log_path = _get_mcp_log_path(session_id)
    config_overrides = _parse_mcp_config_overrides(agent.mcp_config)
    config_text = (agent.mcp_config or "").lower()
    sandbox_permissions = config_overrides.get("sandbox_permissions")
    network_access_override = config_overrides.get("network_access")
    sandbox_override = config_overrides.get("sandbox")
    network_access_enabled = bool((isinstance(network_access_override, str) and network_access_override.lower() == "enabled")
                                  or (isinstance(sandbox_permissions, list) and "network-full-access" in sandbox_permissions)
                                  or re.search(r"network_access\\s*=\\s*\"?enabled\"?", config_text)
                                  or re.search(r"features\\.network\\s*=\\s*true", config_text) or "network-full-access" in config_text)
    sandbox_mode = "workspace-write"
    if isinstance(sandbox_override, str) and sandbox_override:
        sandbox_mode = sandbox_override
    elif network_access_enabled:
        sandbox_mode = "danger-full-access"
    result = _send_tool_call(
        mcp,
        prompt=prompt,
        cwd=cwd,
        conversation_id=conversation_id,
        stream_timeout_seconds=timeout_seconds,
        network_access_enabled=network_access_enabled,
        config_overrides=config_overrides,
        sandbox_mode=sandbox_mode,
        log_path=log_path,
    )
    if mcp.proc.stderr:
        try:
            stderr_text = mcp.proc.stderr.read1(4096).decode("utf-8", errors="ignore").strip()
        except Exception:
            stderr_text = ""
        if stderr_text:
            logger.warning("MCP stderr: %s", stderr_text)
    return result
