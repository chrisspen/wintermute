"""Unit tests for Codex MCP client (persistent subprocess model)."""

import json
import subprocess
import sys
import unittest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch


# Create mock modules before importing wintermute modules
# This allows tests to run without sqlalchemy installed
# Save original modules so we can restore them after import
_original_db = sys.modules.get('wintermute.db')
_original_runner = sys.modules.get('wintermute.runner')


@dataclass(frozen=True)
class MockAgentRecord:
    """Mock AgentRecord for testing without db dependencies."""
    id: str
    name: str
    slug: str
    command: str
    session_mode: str
    required_ssh_options: Optional[str]
    env_vars: Optional[str]
    mcp_config: Optional[str]
    trust_level: Optional[str]
    input_echo_prefix: Optional[str]
    response_prefix: Optional[str]
    created_at: str
    updated_at: str


@dataclass
class MockSSHSpec:
    """Mock SSHSpec for testing without runner dependencies."""
    host: str
    user: str
    port: int
    options: list


# Mock the wintermute.db module
mock_db = MagicMock()
mock_db.AgentRecord = MockAgentRecord
sys.modules['wintermute.db'] = mock_db

# Mock the wintermute.runner module
mock_runner = MagicMock()
mock_runner.SSHSpec = MockSSHSpec
sys.modules['wintermute.runner'] = mock_runner

# Now import the module under test
from wintermute.mcp_client import (
    MCPResult,
    MCPProcess,
    _build_mcp_shell,
    _is_local_host,
    _parse_toml_value,
    _set_nested_config,
    _parse_mcp_config_overrides,
    _send_json,
    _next_id,
    _collect_response,
    _extract_assistant_text,
    _pick_tool_name,
    get_mcp_process,
    close_mcp_process,
    poll_codex_mcp,
    run_codex_mcp,
    _MCP_PROCESSES,
    _MCP_LOCK,
)

# Restore original modules after import to avoid polluting other tests
if _original_db is not None:
    sys.modules['wintermute.db'] = _original_db
else:
    del sys.modules['wintermute.db']

if _original_runner is not None:
    sys.modules['wintermute.runner'] = _original_runner
else:
    del sys.modules['wintermute.runner']


def _make_agent(
    command: str = "codex",
    mcp_config: str = "",
    env_vars: str = "",
) -> MockAgentRecord:
    """Create a minimal AgentRecord for testing."""
    return MockAgentRecord(
        id="agent-1",
        name="Test Agent",
        slug="test-agent",
        command=command,
        session_mode="mcp",
        required_ssh_options=None,
        env_vars=env_vars,
        mcp_config=mcp_config,
        trust_level=None,
        input_echo_prefix=None,
        response_prefix=None,
        created_at="2026-01-10T00:00:00Z",
        updated_at="2026-01-10T00:00:00Z",
    )


def _make_spec(
    host: str = "localhost",
    user: str = "testuser",
    port: int = 22,
) -> MockSSHSpec:
    """Create a minimal SSHSpec for testing."""
    return MockSSHSpec(host=host, user=user, port=port, options=[])


class TestIsLocalHost(unittest.TestCase):
    def test_localhost(self):
        self.assertTrue(_is_local_host("localhost"))
        self.assertTrue(_is_local_host("LOCALHOST"))
        self.assertTrue(_is_local_host("  localhost  "))

    def test_loopback_ipv4(self):
        self.assertTrue(_is_local_host("127.0.0.1"))

    def test_loopback_ipv6(self):
        self.assertTrue(_is_local_host("::1"))

    def test_remote_host(self):
        self.assertFalse(_is_local_host("remote.example.com"))
        self.assertFalse(_is_local_host("192.168.1.100"))


class TestBuildMcpShell(unittest.TestCase):
    def test_basic_command(self):
        agent = _make_agent()
        shell = _build_mcp_shell(agent)
        self.assertIn("codex", shell)
        self.assertIn("mcp-server", shell)

    def test_with_env_vars(self):
        agent = _make_agent(env_vars="TERM=dumb NO_COLOR=1")
        shell = _build_mcp_shell(agent)
        self.assertIn("env TERM=dumb NO_COLOR=1", shell)

    def test_with_mcp_config(self):
        agent = _make_agent(mcp_config="--model gpt-4")
        shell = _build_mcp_shell(agent)
        self.assertIn("--model gpt-4", shell)

    def test_custom_command(self):
        agent = _make_agent(command="/usr/local/bin/codex")
        shell = _build_mcp_shell(agent)
        self.assertIn("/usr/local/bin/codex", shell)

    def test_includes_stdbuf(self):
        agent = _make_agent()
        shell = _build_mcp_shell(agent)
        self.assertIn("stdbuf", shell)

    def test_network_full_access_adds_network_config(self):
        agent = _make_agent(mcp_config="--sandbox-permissions network-full-access")
        shell = _build_mcp_shell(agent)
        self.assertIn("network-full-access", shell)
        self.assertIn("network_access", shell)


class TestParseTomlValue(unittest.TestCase):
    def test_parse_string(self):
        self.assertEqual(_parse_toml_value('"hello"'), "hello")

    def test_parse_integer(self):
        self.assertEqual(_parse_toml_value("42"), 42)

    def test_parse_boolean(self):
        self.assertEqual(_parse_toml_value("true"), True)
        self.assertEqual(_parse_toml_value("false"), False)

    def test_parse_array(self):
        self.assertEqual(_parse_toml_value('["a", "b"]'), ["a", "b"])

    def test_invalid_returns_raw(self):
        self.assertEqual(_parse_toml_value("not valid toml {{"), "not valid toml {{")


class TestSetNestedConfig(unittest.TestCase):
    def test_simple_key(self):
        config = {}
        _set_nested_config(config, "key", "value")
        self.assertEqual(config, {"key": "value"})

    def test_nested_key(self):
        config = {}
        _set_nested_config(config, "outer.inner", "value")
        self.assertEqual(config, {"outer": {"inner": "value"}})

    def test_deeply_nested_key(self):
        config = {}
        _set_nested_config(config, "a.b.c.d", "value")
        self.assertEqual(config, {"a": {"b": {"c": {"d": "value"}}}})

    def test_overwrites_existing(self):
        config = {"key": "old"}
        _set_nested_config(config, "key", "new")
        self.assertEqual(config, {"key": "new"})


class TestParseMcpConfigOverrides(unittest.TestCase):
    def test_empty_config(self):
        self.assertEqual(_parse_mcp_config_overrides(None), {})
        self.assertEqual(_parse_mcp_config_overrides(""), {})

    def test_single_config(self):
        result = _parse_mcp_config_overrides('-c network_access="enabled"')
        self.assertEqual(result, {"network_access": "enabled"})

    def test_multiple_configs(self):
        result = _parse_mcp_config_overrides('-c key1="value1" -c key2="value2"')
        self.assertEqual(result, {"key1": "value1", "key2": "value2"})

    def test_nested_config(self):
        result = _parse_mcp_config_overrides('-c outer.inner="value"')
        self.assertEqual(result, {"outer": {"inner": "value"}})

    def test_long_form_config_flag(self):
        result = _parse_mcp_config_overrides('--config key="value"')
        self.assertEqual(result, {"key": "value"})

    def test_ignores_non_config_flags(self):
        result = _parse_mcp_config_overrides('--model gpt-4 -c key="value"')
        self.assertEqual(result, {"key": "value"})


class TestMCPResult(unittest.TestCase):
    def test_dataclass_creation(self):
        result = MCPResult(
            response_text="Hello",
            conversation_id="conv-123",
            error=None,
        )
        self.assertEqual(result.response_text, "Hello")
        self.assertEqual(result.conversation_id, "conv-123")
        self.assertIsNone(result.error)

    def test_frozen(self):
        result = MCPResult(response_text="Hello", conversation_id="conv-123")
        with self.assertRaises(AttributeError):
            result.response_text = "Changed"


class TestMCPProcess(unittest.TestCase):
    def test_dataclass_creation(self):
        mock_proc = MagicMock()
        proc = MCPProcess(proc=mock_proc, next_id=1, initialized=False, tools=None, last_used=100.0)
        self.assertEqual(proc.next_id, 1)
        self.assertFalse(proc.initialized)
        self.assertEqual(proc.last_used, 100.0)


class TestNextId(unittest.TestCase):
    def test_increments_id(self):
        mock_proc = MagicMock()
        mcp = MCPProcess(proc=mock_proc, next_id=1)
        self.assertEqual(_next_id(mcp), 1)
        self.assertEqual(_next_id(mcp), 2)
        self.assertEqual(_next_id(mcp), 3)
        self.assertEqual(mcp.next_id, 4)


class TestSendJson(unittest.TestCase):
    def test_sends_json_message(self):
        mock_proc = MagicMock()
        mock_stdin = MagicMock()
        mock_proc.stdin = mock_stdin

        _send_json(mock_proc, {"key": "value"})

        mock_stdin.write.assert_called_once()
        mock_stdin.flush.assert_called_once()

        # Verify the message format
        written_data = mock_stdin.write.call_args[0][0]
        message = json.loads(written_data.decode("utf-8").strip())
        self.assertEqual(message, {"key": "value"})

    def test_raises_on_closed_stdin(self):
        mock_proc = MagicMock()
        mock_proc.stdin = None

        with self.assertRaises(RuntimeError):
            _send_json(mock_proc, {"key": "value"})


class TestCollectResponse(unittest.TestCase):
    def test_empty_messages(self):
        response_text, conv_id, error = _collect_response([])
        self.assertEqual(response_text, "")
        self.assertIsNone(conv_id)
        self.assertIsNone(error)

    def test_extracts_result_text(self):
        messages = [
            {"result": {"text": "Hello from Codex"}}
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertEqual(response_text, "Hello from Codex")

    def test_extracts_output_text(self):
        messages = [
            {"result": {"output_text": "Output text here"}}
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertEqual(response_text, "Output text here")

    def test_extracts_conversation_id(self):
        messages = [
            {"result": {"conversationId": "conv-abc-123", "text": "Hello"}}
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertEqual(conv_id, "conv-abc-123")

    def test_extracts_error(self):
        messages = [
            {"error": {"code": -32600, "message": "Invalid request"}}
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertIsNotNone(error)
        self.assertIn("Invalid request", error)

    def test_extracts_codex_event_text(self):
        messages = [
            {
                "method": "codex/event",
                "params": {"message": "Event message here"}
            }
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertEqual(response_text, "Event message here")

    def test_extracts_session_id_from_event(self):
        messages = [
            {
                "method": "codex/event",
                "params": {"session_id": "sess-xyz", "text": "Hello"}
            }
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertEqual(conv_id, "sess-xyz")

    def test_extracts_raw_response_item(self):
        messages = [
            {
                "method": "codex/event",
                "params": {
                    "msg": {
                        "type": "raw_response_item",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Raw response text"}]
                        }
                    }
                }
            }
        ]
        response_text, conv_id, error = _collect_response(messages)
        self.assertEqual(response_text, "Raw response text")


class TestExtractAssistantText(unittest.TestCase):
    def test_non_codex_event(self):
        texts, conv_id, error = _extract_assistant_text({"method": "other"})
        self.assertEqual(texts, [])
        self.assertIsNone(conv_id)
        self.assertIsNone(error)

    def test_error_payload(self):
        texts, conv_id, error = _extract_assistant_text({"error": {"message": "Error"}})
        self.assertEqual(texts, [])
        self.assertIsNotNone(error)

    def test_extracts_raw_response_item(self):
        payload = {
            "method": "codex/event",
            "params": {
                "session_id": "sess-123",
                "msg": {
                    "type": "raw_response_item",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "First block"},
                            {"type": "text", "text": "Second block"}
                        ]
                    }
                }
            }
        }
        texts, conv_id, error = _extract_assistant_text(payload)
        self.assertEqual(texts, ["First block", "Second block"])
        self.assertEqual(conv_id, "sess-123")
        self.assertIsNone(error)

    def test_extracts_assistant_message(self):
        payload = {
            "method": "codex/event",
            "params": {
                "msg": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Assistant response"}]
                }
            }
        }
        texts, conv_id, error = _extract_assistant_text(payload)
        self.assertEqual(texts, ["Assistant response"])

    def test_extracts_assistant_message_type(self):
        payload = {
            "method": "codex/event",
            "params": {
                "msg": {
                    "type": "assistant_message",
                    "text": "Direct text"
                }
            }
        }
        texts, conv_id, error = _extract_assistant_text(payload)
        self.assertEqual(texts, ["Direct text"])


class TestPickToolName(unittest.TestCase):
    def test_no_conversation_uses_codex(self):
        mock_proc = MagicMock()
        mcp = MCPProcess(proc=mock_proc, tools=[{"name": "codex"}, {"name": "codex-reply"}])
        self.assertEqual(_pick_tool_name(mcp, None), "codex")

    def test_with_conversation_and_reply_tool(self):
        mock_proc = MagicMock()
        mcp = MCPProcess(proc=mock_proc, tools=[{"name": "codex"}, {"name": "codex-reply"}])
        self.assertEqual(_pick_tool_name(mcp, "conv-123"), "codex-reply")

    def test_with_conversation_without_reply_tool(self):
        mock_proc = MagicMock()
        mcp = MCPProcess(proc=mock_proc, tools=[{"name": "codex"}])
        self.assertEqual(_pick_tool_name(mcp, "conv-123"), "codex")

    def test_empty_tools(self):
        mock_proc = MagicMock()
        mcp = MCPProcess(proc=mock_proc, tools=[])
        self.assertEqual(_pick_tool_name(mcp, None), "codex")

    def test_none_tools(self):
        mock_proc = MagicMock()
        mcp = MCPProcess(proc=mock_proc, tools=None)
        self.assertEqual(_pick_tool_name(mcp, None), "codex")


class TestCloseMcpProcess(unittest.TestCase):
    def setUp(self):
        # Clear the global process dict
        with _MCP_LOCK:
            _MCP_PROCESSES.clear()

    def test_close_nonexistent_process(self):
        # Should not raise
        close_mcp_process("nonexistent-session")

    def test_close_existing_process(self):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()

        with _MCP_LOCK:
            _MCP_PROCESSES["test-session"] = MCPProcess(proc=mock_proc)

        close_mcp_process("test-session")

        mock_proc.terminate.assert_called_once()
        self.assertNotIn("test-session", _MCP_PROCESSES)


class TestPollCodexMcp(unittest.TestCase):
    def setUp(self):
        with _MCP_LOCK:
            _MCP_PROCESSES.clear()

    def test_poll_nonexistent_process(self):
        result = poll_codex_mcp("nonexistent-session", timeout_seconds=1)
        self.assertEqual(result.response_text, "")
        self.assertIn("not found", result.error)

    def test_poll_exited_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Process exited

        with _MCP_LOCK:
            _MCP_PROCESSES["test-session"] = MCPProcess(proc=mock_proc)

        result = poll_codex_mcp("test-session", timeout_seconds=1)
        self.assertEqual(result.response_text, "")
        self.assertIn("exited", result.error)


class TestRunCodexMcp(unittest.TestCase):
    def setUp(self):
        with _MCP_LOCK:
            _MCP_PROCESSES.clear()

    @patch("wintermute.mcp_client._start_mcp_process")
    @patch("wintermute.mcp_client._ensure_initialized")
    @patch("wintermute.mcp_client._read_stream_response")
    @patch("wintermute.mcp_client.getpass.getuser")
    def test_creates_process_and_sends_prompt(self, mock_getuser, mock_read, mock_init, mock_start):
        mock_getuser.return_value = "testuser"

        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is running
        mock_proc.stdin = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read1.return_value = b""
        mock_start.return_value = mock_proc

        # Setup mock initialization (returns None for success)
        mock_init.return_value = None

        # Setup mock response
        mock_read.return_value = ("Hello from Codex", "conv-123", None, [])

        agent = _make_agent()
        spec = _make_spec()

        # Pre-populate initialized MCP process
        with _MCP_LOCK:
            mcp = MCPProcess(proc=mock_proc, initialized=True, tools=[{"name": "codex"}])
            _MCP_PROCESSES["winter-sess-1"] = mcp

        result = run_codex_mcp(
            spec,
            agent,
            session_id="winter-sess-1",
            prompt="Hello Codex",
            cwd="/tmp/test",
            conversation_id=None,
        )

        self.assertEqual(result.response_text, "Hello from Codex")
        self.assertEqual(result.conversation_id, "conv-123")
        self.assertIsNone(result.error)

    @patch("wintermute.mcp_client._start_mcp_process")
    @patch("wintermute.mcp_client._ensure_initialized")
    @patch("wintermute.mcp_client.getpass.getuser")
    def test_handles_init_failure(self, mock_getuser, mock_init, mock_start):
        mock_getuser.return_value = "testuser"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_start.return_value = mock_proc

        # Initialization fails
        mock_init.return_value = "Failed to initialize"

        agent = _make_agent()
        spec = _make_spec()

        result = run_codex_mcp(
            spec,
            agent,
            session_id="winter-sess-2",
            prompt="Hello",
            cwd="/tmp/test",
            conversation_id=None,
        )

        self.assertEqual(result.response_text, "")
        self.assertIn("initialize", result.error.lower())

    @patch("wintermute.mcp_client._start_mcp_process")
    @patch("wintermute.mcp_client._read_stream_response")
    @patch("wintermute.mcp_client.getpass.getuser")
    def test_reuses_existing_process(self, mock_getuser, mock_read, mock_start):
        mock_getuser.return_value = "testuser"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read1.return_value = b""
        mock_start.return_value = mock_proc

        mock_read.return_value = ("Response 1", "conv-1", None, [])

        agent = _make_agent()
        spec = _make_spec()

        # Pre-populate initialized MCP process
        with _MCP_LOCK:
            mcp = MCPProcess(proc=mock_proc, initialized=True, tools=[{"name": "codex"}])
            _MCP_PROCESSES["reuse-sess"] = mcp

        # First call
        run_codex_mcp(spec, agent, session_id="reuse-sess", prompt="First", cwd="/tmp", conversation_id=None)

        # Second call should reuse the process
        mock_read.return_value = ("Response 2", "conv-1", None, [])
        run_codex_mcp(spec, agent, session_id="reuse-sess", prompt="Second", cwd="/tmp", conversation_id="conv-1")

        # Process should not be started again (already existed)
        self.assertEqual(mock_start.call_count, 0)


class TestJsonRpcParsing(unittest.TestCase):
    """Test parsing of JSON-RPC message formats."""

    def test_parse_initialize_response(self):
        """Test that we can identify initialize responses."""
        init_response = {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "codex-mcp", "version": "1.0.0"}
            }
        }
        self.assertEqual(init_response["id"], 1)
        self.assertIn("result", init_response)

    def test_parse_tools_list_response(self):
        """Test parsing tools/list response."""
        tools_response = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "tools": [
                    {"name": "codex", "description": "Run Codex"},
                    {"name": "codex-reply", "description": "Reply in conversation"}
                ]
            }
        }
        tools = tools_response["result"]["tools"]
        self.assertEqual(len(tools), 2)
        self.assertEqual(tools[0]["name"], "codex")

    def test_parse_tool_call_response(self):
        """Test parsing tools/call response."""
        call_response = {
            "jsonrpc": "2.0",
            "id": 3,
            "result": {
                "content": [{"type": "text", "text": "Task completed."}],
                "conversationId": "conv-abc-123"
            }
        }
        result = call_response["result"]
        self.assertEqual(result["conversationId"], "conv-abc-123")
        self.assertEqual(result["content"][0]["text"], "Task completed.")

    def test_parse_error_response(self):
        """Test parsing error response."""
        error_response = {
            "jsonrpc": "2.0",
            "id": 3,
            "error": {
                "code": -32600,
                "message": "Invalid Request"
            }
        }
        self.assertIn("error", error_response)
        self.assertEqual(error_response["error"]["code"], -32600)


if __name__ == "__main__":
    unittest.main()
