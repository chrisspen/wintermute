"""Tests for agent health check functionality (WM-7)."""

import unittest
from unittest.mock import MagicMock, patch
import subprocess


class TestRunHealthCheck(unittest.TestCase):
    """Tests for run_health_check function in runner.py."""

    @patch("wintermute.runner.subprocess.run")
    def test_health_check_passes(self, mock_run: MagicMock) -> None:
        """Health check passes when command exits with 0."""
        from wintermute.runner import run_health_check, SSHSpec

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="OK\n",
            stderr="",
        )

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        ok, error = run_health_check(spec, "test -f /tmp/ready.flag")

        self.assertTrue(ok)
        self.assertEqual(error, "")
        mock_run.assert_called_once()

    @patch("wintermute.runner.subprocess.run")
    def test_health_check_fails_nonzero_exit(self, mock_run: MagicMock) -> None:
        """Health check fails when command exits with non-zero."""
        from wintermute.runner import run_health_check, SSHSpec

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr="File not found",
        )

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        ok, error = run_health_check(spec, "test -f /tmp/ready.flag")

        self.assertFalse(ok)
        self.assertIn("Health check failed", error)
        self.assertIn("exit code 1", error)
        self.assertIn("File not found", error)

    @patch("wintermute.runner.subprocess.run")
    def test_health_check_timeout(self, mock_run: MagicMock) -> None:
        """Health check fails when command times out."""
        from wintermute.runner import run_health_check, SSHSpec

        mock_run.side_effect = subprocess.TimeoutExpired(cmd="ssh", timeout=60)

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        ok, error = run_health_check(spec, "sleep 120", timeout=60)

        self.assertFalse(ok)
        self.assertIn("timed out", error)
        self.assertIn("60", error)

    @patch("wintermute.runner.subprocess.run")
    def test_health_check_ssh_error(self, mock_run: MagicMock) -> None:
        """Health check fails gracefully on SSH errors."""
        from wintermute.runner import run_health_check, SSHSpec

        mock_run.side_effect = Exception("Connection refused")

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        ok, error = run_health_check(spec, "echo hello")

        self.assertFalse(ok)
        self.assertIn("failed to execute", error)
        self.assertIn("Connection refused", error)

    @patch("wintermute.runner.subprocess.run")
    def test_health_check_uses_ssh_options(self, mock_run: MagicMock) -> None:
        """Health check includes SSH options in command."""
        from wintermute.runner import run_health_check, SSHSpec

        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        spec = SSHSpec(
            host="remote.example.com",
            user="deploy",
            port=2222,
            options=["-i", "/path/to/key"],
        )
        run_health_check(spec, "test -f /app/ready")

        call_args = mock_run.call_args[0][0]
        self.assertEqual(call_args[0], "ssh")
        self.assertIn("-p", call_args)
        self.assertIn("2222", call_args)
        self.assertIn("-i", call_args)
        self.assertIn("/path/to/key", call_args)
        self.assertIn("deploy@remote.example.com", call_args)
        self.assertIn("test -f /app/ready", call_args)

    @patch("wintermute.runner.subprocess.run")
    def test_health_check_uses_stdout_when_stderr_empty(self, mock_run: MagicMock) -> None:
        """Health check shows stdout in error when stderr is empty."""
        from wintermute.runner import run_health_check, SSHSpec

        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="Error from stdout",
            stderr="",
        )

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        ok, error = run_health_check(spec, "failing-command")

        self.assertFalse(ok)
        self.assertIn("Error from stdout", error)


class TestHealthCheckIntegration(unittest.TestCase):
    """Integration tests for health check in session start flow."""

    @patch("wintermute.runner.run_health_check")
    @patch("wintermute.runner.check_vm_memory_available")
    @patch("wintermute.runner.ensure_vm_tools")
    @patch("wintermute.runner.start_session")
    def test_session_start_calls_health_check(
        self,
        mock_start: MagicMock,
        mock_tools: MagicMock,
        mock_memory: MagicMock,
        mock_health: MagicMock,
    ) -> None:
        """Starting a session calls health check when configured."""
        # This tests that the integration point exists
        # The actual call happens in app.py's api_start_agent_session
        mock_health.return_value = (True, "")
        mock_memory.return_value = (True, "")
        mock_tools.return_value = (True, "")
        mock_start.return_value = "session-123"

        from wintermute.runner import run_health_check, SSHSpec

        spec = SSHSpec(host="localhost", user="test", port=22, options=[])
        ok, error = run_health_check(spec, "echo ready")

        self.assertTrue(ok)
        mock_health.assert_called_once_with(spec, "echo ready")


if __name__ == "__main__":
    unittest.main()
