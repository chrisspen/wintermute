"""Unit tests for Claude Code CLI client (persistent subprocess model)."""

import json
import subprocess
import sys
import unittest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch, PropertyMock

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
from wintermute.claude_client import (
    ClaudeResult,
    ClaudeProcess,
    _build_claude_shell,
    _is_local_host,
    _send_user_message,
    _read_stream_response,
    get_claude_process,
    close_claude_process,
    poll_claude,
    run_claude_prompt,
    _CLAUDE_PROCESSES,
    _CLAUDE_LOCK,
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
    command: str = "claude",
    mcp_config: str = "",
    env_vars: str = "",
) -> MockAgentRecord:
    """Create a minimal AgentRecord for testing."""
    return MockAgentRecord(
        id="agent-1",
        name="Test Agent",
        slug="test-agent",
        command=command,
        session_mode="claude",
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


class TestBuildClaudeShell(unittest.TestCase):
    def test_basic_command(self):
        agent = _make_agent()
        shell = _build_claude_shell(agent, "/tmp/test")
        self.assertIn("claude", shell)
        self.assertIn("-p", shell)
        self.assertIn("--input-format", shell)
        self.assertIn("stream-json", shell)
        self.assertIn("--output-format", shell)
        self.assertIn("--cwd", shell)
        self.assertIn("/tmp/test", shell)

    def test_with_permission_mode(self):
        agent = _make_agent(mcp_config="--permission-mode bypassPermissions")
        shell = _build_claude_shell(agent, "/tmp/test")
        self.assertIn("--permission-mode", shell)
        self.assertIn("bypassPermissions", shell)

    def test_with_env_vars(self):
        agent = _make_agent(env_vars="TERM=dumb NO_COLOR=1")
        shell = _build_claude_shell(agent, "/tmp/test")
        self.assertIn("env TERM=dumb NO_COLOR=1", shell)

    def test_custom_command(self):
        agent = _make_agent(command="/usr/local/bin/claude")
        shell = _build_claude_shell(agent, "/tmp/test")
        self.assertIn("/usr/local/bin/claude", shell)

    def test_includes_stdbuf(self):
        agent = _make_agent()
        shell = _build_claude_shell(agent, "/tmp/test")
        self.assertIn("stdbuf", shell)


class TestClaudeResult(unittest.TestCase):
    def test_dataclass_creation(self):
        result = ClaudeResult(
            response_text="Hello",
            session_id="sess-123",
            usage={"input_tokens": 10},
            total_cost_usd=0.001,
            duration_ms=500,
            error=None,
        )
        self.assertEqual(result.response_text, "Hello")
        self.assertEqual(result.session_id, "sess-123")
        self.assertIsNone(result.error)

    def test_frozen(self):
        result = ClaudeResult(response_text="Hello", session_id="sess-123")
        with self.assertRaises(AttributeError):
            result.response_text = "Changed"


class TestClaudeProcess(unittest.TestCase):
    def test_dataclass_creation(self):
        mock_proc = MagicMock()
        proc = ClaudeProcess(proc=mock_proc, session_id="sess-123", last_used=100.0)
        self.assertEqual(proc.session_id, "sess-123")
        self.assertEqual(proc.last_used, 100.0)


class TestSendUserMessage(unittest.TestCase):
    def test_sends_json_message(self):
        mock_proc = MagicMock()
        mock_stdin = MagicMock()
        mock_proc.stdin = mock_stdin

        _send_user_message(mock_proc, "Hello Claude")

        mock_stdin.write.assert_called_once()
        mock_stdin.flush.assert_called_once()

        # Verify the message format
        written_data = mock_stdin.write.call_args[0][0]
        message = json.loads(written_data.decode("utf-8").strip())
        self.assertEqual(message["type"], "user")
        self.assertEqual(message["message"]["role"], "user")
        self.assertEqual(message["message"]["content"], "Hello Claude")

    def test_raises_on_closed_stdin(self):
        mock_proc = MagicMock()
        mock_proc.stdin = None

        with self.assertRaises(RuntimeError):
            _send_user_message(mock_proc, "Hello")


class TestCloseClaudeProcess(unittest.TestCase):
    def setUp(self):
        # Clear the global process dict
        with _CLAUDE_LOCK:
            _CLAUDE_PROCESSES.clear()

    def test_close_nonexistent_process(self):
        # Should not raise
        close_claude_process("nonexistent-session")

    def test_close_existing_process(self):
        mock_proc = MagicMock()
        mock_proc.stdin = MagicMock()

        with _CLAUDE_LOCK:
            _CLAUDE_PROCESSES["test-session"] = ClaudeProcess(proc=mock_proc)

        close_claude_process("test-session")

        mock_proc.terminate.assert_called_once()
        self.assertNotIn("test-session", _CLAUDE_PROCESSES)


class TestPollClaude(unittest.TestCase):
    def setUp(self):
        with _CLAUDE_LOCK:
            _CLAUDE_PROCESSES.clear()

    def test_poll_nonexistent_process(self):
        result = poll_claude("nonexistent-session", timeout_seconds=1)
        self.assertEqual(result.response_text, "")
        self.assertIn("not found", result.error)

    def test_poll_exited_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # Process exited

        with _CLAUDE_LOCK:
            _CLAUDE_PROCESSES["test-session"] = ClaudeProcess(proc=mock_proc)

        result = poll_claude("test-session", timeout_seconds=1)
        self.assertEqual(result.response_text, "")
        self.assertIn("exited", result.error)


class TestRunClaudePrompt(unittest.TestCase):
    def setUp(self):
        with _CLAUDE_LOCK:
            _CLAUDE_PROCESSES.clear()

    @patch("wintermute.claude_client._start_claude_process")
    @patch("wintermute.claude_client._read_stream_response")
    @patch("wintermute.claude_client.getpass.getuser")
    def test_creates_process_and_sends_prompt(self, mock_getuser, mock_read, mock_start):
        mock_getuser.return_value = "testuser"

        # Setup mock process
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # Process is running
        mock_proc.stdin = MagicMock()
        mock_start.return_value = mock_proc

        # Setup mock response
        mock_read.return_value = ("Hello from Claude", "claude-sess-123", None, None)

        agent = _make_agent()
        spec = _make_spec()

        result = run_claude_prompt(
            spec,
            agent,
            session_id="winter-sess-1",
            prompt="Hello Claude",
            cwd="/tmp/test",
        )

        self.assertEqual(result.response_text, "Hello from Claude")
        self.assertEqual(result.session_id, "claude-sess-123")
        self.assertIsNone(result.error)

        # Verify message was sent
        mock_proc.stdin.write.assert_called()
        mock_proc.stdin.flush.assert_called()

    @patch("wintermute.claude_client._start_claude_process")
    @patch("wintermute.claude_client.getpass.getuser")
    def test_handles_process_exit(self, mock_getuser, mock_start):
        mock_getuser.return_value = "testuser"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Process exited
        mock_start.return_value = mock_proc

        agent = _make_agent()
        spec = _make_spec()

        result = run_claude_prompt(
            spec,
            agent,
            session_id="winter-sess-2",
            prompt="Hello",
            cwd="/tmp/test",
        )

        self.assertEqual(result.response_text, "")
        self.assertIn("exited", result.error)

    @patch("wintermute.claude_client._start_claude_process")
    @patch("wintermute.claude_client._read_stream_response")
    @patch("wintermute.claude_client.getpass.getuser")
    def test_reuses_existing_process(self, mock_getuser, mock_read, mock_start):
        mock_getuser.return_value = "testuser"

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.stdin = MagicMock()
        mock_start.return_value = mock_proc

        mock_read.return_value = ("Response 1", "sess-1", None, None)

        agent = _make_agent()
        spec = _make_spec()

        # First call
        run_claude_prompt(spec, agent, session_id="reuse-sess", prompt="First", cwd="/tmp")

        # Second call should reuse the process
        mock_read.return_value = ("Response 2", "sess-1", None, None)
        run_claude_prompt(spec, agent, session_id="reuse-sess", prompt="Second", cwd="/tmp")

        # Process should only be started once
        self.assertEqual(mock_start.call_count, 1)


class TestStreamingJsonParsing(unittest.TestCase):
    """Test parsing of Claude's streaming JSON output format."""

    def test_parse_init_message(self):
        """Test that we can identify session init messages."""
        init_msg = {
            "type": "system",
            "subtype": "init",
            "session_id": "test-session-123",
            "tools": ["Read", "Edit"],
            "model": "claude-opus-4-5",
        }
        # This is a structural test - the actual parsing happens in _read_stream_response
        self.assertEqual(init_msg["type"], "system")
        self.assertEqual(init_msg["subtype"], "init")
        self.assertEqual(init_msg["session_id"], "test-session-123")

    def test_parse_assistant_message(self):
        """Test parsing assistant text blocks."""
        assistant_msg = {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Hello! I can help with that."},
                    {"type": "tool_use", "id": "tool-1", "name": "Read"},
                ]
            }
        }
        content = assistant_msg["message"]["content"]
        texts = [b["text"] for b in content if b.get("type") == "text"]
        self.assertEqual(texts, ["Hello! I can help with that."])

    def test_parse_result_message(self):
        """Test parsing result/completion messages."""
        result_msg = {
            "type": "result",
            "subtype": "success",
            "session_id": "test-session-123",
            "result": "Task completed successfully.",
            "usage": {"input_tokens": 100, "output_tokens": 50},
            "total_cost_usd": 0.0015,
            "duration_ms": 2500,
            "is_error": False,
        }
        self.assertEqual(result_msg["type"], "result")
        self.assertFalse(result_msg["is_error"])
        self.assertEqual(result_msg["result"], "Task completed successfully.")


if __name__ == "__main__":
    unittest.main()
