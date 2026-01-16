"""Unit tests for Agent Session API endpoints (start/stop/status)."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app


def _create_test_user(db: Database, username: str = "testuser", password: str = "testpass") -> str:
    """Create a test user with proper password hash."""
    salt = os.urandom(16)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    password_hash = base64.b64encode(
        hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )
    ).decode("ascii")
    user_id = str(uuid.uuid4())
    db.insert_user(
        user_id=user_id,
        username=username,
        password_hash=password_hash,
        salt=salt_b64,
    )
    return user_id


def _mock_subprocess_run(cmd, **kwargs):
    """Mock subprocess.run for SSH/SCP commands."""
    result = MagicMock()
    result.returncode = 0
    result.stderr = ""

    # Check if it's a mktemp command
    cmd_str = " ".join(cmd) if isinstance(cmd, list) else cmd
    if "mktemp" in cmd_str:
        result.stdout = "/tmp/agent_test-agent_mock123\n"
    else:
        result.stdout = ""

    return result


class AgentSessionAPITests(unittest.TestCase):
    """Tests for standalone agent session API."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)

        # Create a test user and login
        _create_test_user(self.db)
        self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )

        # Create a test VM target (required for session start)
        self.vm_target_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_target_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create a test agent with VM target
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_target_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_session_status_no_session(self) -> None:
        """Test session status when no session is running."""
        response = self.client.get(
            f"/api/agents/{self.agent_id}/session-status",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["running"])
        self.assertIsNone(data["session_id"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    def test_start_session(self, mock_start_session, mock_run) -> None:
        """Test starting a standalone session."""
        response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("session_id", data)
        self.assertIn("location", data)
        self.assertIsNotNone(data["session_id"])
        self.assertIsNotNone(data["location"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    def test_session_status_after_start(self, mock_start_session, mock_run) -> None:
        """Test session status after starting a session."""
        # Start a session first
        start_response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        session_id = start_response.json()["session_id"]

        # Check status
        response = self.client.get(
            f"/api/agents/{self.agent_id}/session-status",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["running"])
        self.assertEqual(data["session_id"], session_id)

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    def test_start_session_already_running(self, mock_start_session, mock_run) -> None:
        """Test that starting a session fails if one is already running."""
        # Start first session
        self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )

        # Try to start another session
        response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("already running", response.json()["detail"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    @patch("wintermute.runner.stop_session")
    def test_stop_session(self, mock_stop_session, mock_start_session, mock_run) -> None:
        """Test stopping a running session."""
        # Start a session first
        self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )

        # Stop the session
        response = self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])

    def test_stop_session_no_running_session(self) -> None:
        """Test stopping fails when no session is running."""
        response = self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("No running session", response.json()["detail"])

    @patch("subprocess.run", side_effect=_mock_subprocess_run)
    @patch("wintermute.runner.start_session")
    @patch("wintermute.runner.stop_session")
    def test_session_status_after_stop(self, mock_stop_session, mock_start_session, mock_run) -> None:
        """Test session status returns not running after stop."""
        # Start and then stop a session
        self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )

        # Check status
        response = self.client.get(
            f"/api/agents/{self.agent_id}/session-status",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["running"])

    def test_start_session_agent_not_found(self) -> None:
        """Test starting session for non-existent agent."""
        response = self.client.post(
            "/api/agents/nonexistent-id/start-session",
        )
        self.assertEqual(response.status_code, 404)

    def test_session_status_agent_not_found(self) -> None:
        """Test session status for non-existent agent."""
        response = self.client.get(
            "/api/agents/nonexistent-id/session-status",
        )
        self.assertEqual(response.status_code, 404)

    def test_start_session_no_vm_target(self) -> None:
        """Test starting session for agent without VM target."""
        # Create agent without VM target
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="No VM Agent",
            slug="no-vm-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        response = self.client.post(
            f"/api/agents/{agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no VM target", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
