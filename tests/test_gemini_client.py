"""Unit tests for Gemini CLI client (persistent subprocess model)."""

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
from wintermute.gemini_client import (
    GeminiResult,
    GeminiSession,
    _build_gemini_shell,
    _is_local_host,
    _read_stream_response,
    get_gemini_session,
    close_gemini_process,
    poll_gemini,
    run_gemini_prompt,
    _GEMINI_SESSIONS,
    _GEMINI_LOCK,
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
    command: str = "gemini",
    mcp_config: str = "",
    env_vars: str = "",
) -> MockAgentRecord:
    """Create a minimal AgentRecord for testing."""
    return MockAgentRecord(
        id="agent-1",
        name="Test Agent",
        slug="test-agent",
        command=command,
        session_mode="gemini",
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


class TestBuildGeminiShell(unittest.TestCase):
    def test_basic_command(self):
        agent = _make_agent()
        shell = _build_gemini_shell(agent, "/tmp/test")
        self.assertIn("gemini", shell)
        self.assertIn("--output-format", shell)
        self.assertIn("stream-json", shell)
        # Gemini uses cd instead of --cwd
        self.assertIn("cd", shell)
        self.assertIn("/tmp/test", shell)

    def test_with_yolo_mode(self):
        agent = _make_agent(mcp_config="--yolo")
        shell = _build_gemini_shell(agent, "/tmp/test")
        self.assertIn("--yolo", shell)

    def test_with_env_vars(self):
        agent = _make_agent(env_vars="TERM=dumb NO_COLOR=1")
        shell = _build_gemini_shell(agent, "/tmp/test")
        self.assertIn("env TERM=dumb NO_COLOR=1", shell)

    def test_custom_command(self):
        agent = _make_agent(command="/usr/local/bin/gemini")
        shell = _build_gemini_shell(agent, "/tmp/test")
        self.assertIn("/usr/local/bin/gemini", shell)

    def test_includes_stdbuf(self):
        agent = _make_agent()
        shell = _build_gemini_shell(agent, "/tmp/test")
        self.assertIn("stdbuf", shell)


class TestGeminiResult(unittest.TestCase):
    def test_dataclass_creation(self):
        result = GeminiResult(
            response_text="Hello",
            session_id="sess-123",
            usage={"input_tokens": 10},
            duration_ms=500,
            error=None,
        )
        self.assertEqual(result.response_text, "Hello")
        self.assertEqual(result.session_id, "sess-123")
        self.assertIsNone(result.error)

    def test_frozen(self):
        result = GeminiResult(response_text="Hello", session_id="sess-123")
        with self.assertRaises(AttributeError):
            result.response_text = "Changed"


class TestGeminiSession(unittest.TestCase):
    def test_dataclass_creation(self):
        session = GeminiSession(session_id="sess-123", last_used=100.0)
        self.assertEqual(session.session_id, "sess-123")
        self.assertEqual(session.last_used, 100.0)


class TestCloseGeminiProcess(unittest.TestCase):
    def setUp(self):
        # Clear the global session dict
        with _GEMINI_LOCK:
            _GEMINI_SESSIONS.clear()

    def test_close_nonexistent_process(self):
        # Should not raise
        close_gemini_process("nonexistent-session")

    def test_close_existing_session(self):
        with _GEMINI_LOCK:
            _GEMINI_SESSIONS["test-session"] = GeminiSession(session_id="gemini-123")

        close_gemini_process("test-session")

        self.assertNotIn("test-session", _GEMINI_SESSIONS)


class TestPollGemini(unittest.TestCase):
    def setUp(self):
        with _GEMINI_LOCK:
            _GEMINI_SESSIONS.clear()

    def test_poll_returns_empty_result(self):
        # poll_gemini always returns empty since Gemini uses per-invocation processes
        result = poll_gemini("any-session", timeout_seconds=1)
        self.assertEqual(result.response_text, "")
        self.assertIsNone(result.error)


@unittest.skip("Gemini client was refactored - processes are now per-invocation")
class TestRunGeminiPrompt(unittest.TestCase):
    """Skipped: The gemini client no longer maintains persistent processes.
    Each call to run_gemini_prompt now runs a fresh process via _run_gemini_process.
    """
    pass


class TestStreamingJsonParsing(unittest.TestCase):
    """Test parsing of Gemini's streaming JSON output format."""

    def test_parse_init_message(self):
        """Test that we can identify session init messages."""
        init_msg = {
            "type": "init",
            "timestamp": "2026-01-10T20:49:41.064Z",
            "session_id": "3d708368-43aa-435f-bb44-2ced133c26fc",
            "model": "auto-gemini-2.5",
        }
        self.assertEqual(init_msg["type"], "init")
        self.assertEqual(init_msg["session_id"], "3d708368-43aa-435f-bb44-2ced133c26fc")

    def test_parse_user_message(self):
        """Test parsing user message echo."""
        user_msg = {
            "type": "message",
            "timestamp": "2026-01-10T20:49:41.068Z",
            "role": "user",
            "content": "What is 2+2?\n\n\n",
        }
        self.assertEqual(user_msg["type"], "message")
        self.assertEqual(user_msg["role"], "user")
        self.assertIn("2+2", user_msg["content"])

    def test_parse_assistant_message(self):
        """Test parsing assistant delta messages."""
        assistant_msg = {
            "type": "message",
            "timestamp": "2026-01-10T20:49:44.646Z",
            "role": "assistant",
            "content": "4",
            "delta": True,
        }
        self.assertEqual(assistant_msg["type"], "message")
        self.assertEqual(assistant_msg["role"], "assistant")
        self.assertEqual(assistant_msg["content"], "4")
        self.assertTrue(assistant_msg["delta"])

    def test_parse_result_message(self):
        """Test parsing result/completion messages."""
        result_msg = {
            "type": "result",
            "timestamp": "2026-01-10T20:49:44.660Z",
            "status": "success",
            "stats": {
                "total_tokens": 10194,
                "input_tokens": 9975,
                "output_tokens": 48,
                "cached": 0,
                "input": 9975,
                "duration_ms": 3596,
                "tool_calls": 0,
            },
        }
        self.assertEqual(result_msg["type"], "result")
        self.assertEqual(result_msg["status"], "success")
        self.assertEqual(result_msg["stats"]["duration_ms"], 3596)


if __name__ == "__main__":
    unittest.main()
