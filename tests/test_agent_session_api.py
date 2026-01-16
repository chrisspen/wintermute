"""Unit tests for Agent Session API endpoints (start/stop/status)."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid

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
        login_response = self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )
        # TestClient keeps cookies automatically

        # Create a test agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
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

    def test_start_session(self) -> None:
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

    def test_session_status_after_start(self) -> None:
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

    def test_start_session_already_running(self) -> None:
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

    def test_stop_session(self) -> None:
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

    def test_session_status_after_stop(self) -> None:
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


class AgentSessionWithSessionFilesTests(unittest.TestCase):
    """Tests for standalone sessions with session file configs."""

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

        # Create session file config
        self.config_id = str(uuid.uuid4())
        self.db.insert_session_file_config(
            config_id=self.config_id,
            name="Test Config",
        )

        # Create file definitions
        self.agents_md_def_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=self.agents_md_def_id,
            config_id=self.config_id,
            filename="AGENTS.md",
            default_content="# Default AGENTS.md content",
            required=True,
            sync_on_exit=True,
            sort_order=0,
        )
        self.state_md_def_id = str(uuid.uuid4())
        self.db.insert_session_file_definition(
            definition_id=self.state_md_def_id,
            config_id=self.config_id,
            filename="STATE.md",
            default_content="# Default State",
            required=False,
            sync_on_exit=True,
            sort_order=1,
        )

        # Create agent with session file config
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent With Config",
            slug="test-agent-config",
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
        # Set the session file config
        self.db.update_agent(self.agent_id, session_file_config_id=self.config_id)

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_start_creates_session_files_in_workspace(self) -> None:
        """Test that starting a session creates session files in workspace."""
        response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 200)
        workspace = response.json()["location"]

        # Check files were created
        agents_path = os.path.join(workspace, "AGENTS.md")
        state_path = os.path.join(workspace, "STATE.md")
        self.assertTrue(os.path.exists(agents_path))
        self.assertTrue(os.path.exists(state_path))

        # Check content is default
        with open(agents_path, "r") as f:
            self.assertEqual(f.read(), "# Default AGENTS.md content")
        with open(state_path, "r") as f:
            self.assertEqual(f.read(), "# Default State")

    def test_start_uses_saved_session_file_content(self) -> None:
        """Test that starting uses saved session file content if available."""
        # Pre-save a session file for the agent
        self.db.insert_session_file(
            file_id=str(uuid.uuid4()),
            agent_id=self.agent_id,
            definition_id=self.agents_md_def_id,
            content="# Custom saved content",
        )

        response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        self.assertEqual(response.status_code, 200)
        workspace = response.json()["location"]

        # Check custom content was used
        agents_path = os.path.join(workspace, "AGENTS.md")
        with open(agents_path, "r") as f:
            self.assertEqual(f.read(), "# Custom saved content")

    def test_stop_syncs_files_back(self) -> None:
        """Test that stopping a session syncs files back to database."""
        # Start session
        start_response = self.client.post(
            f"/api/agents/{self.agent_id}/start-session",
        )
        workspace = start_response.json()["location"]

        # Modify a file in workspace
        state_path = os.path.join(workspace, "STATE.md")
        with open(state_path, "w") as f:
            f.write("# Modified State Content\n\nSome changes.")

        # Stop session
        self.client.post(
            f"/api/agents/{self.agent_id}/session/stop",
        )

        # Check file was synced back
        session_file = self.db.get_session_file_by_definition(
            self.agent_id, self.state_md_def_id
        )
        self.assertIsNotNone(session_file)
        self.assertEqual(session_file.content, "# Modified State Content\n\nSome changes.")


if __name__ == "__main__":
    unittest.main()
