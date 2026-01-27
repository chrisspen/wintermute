"""Tests for agent working_directory and session_directory fields."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app


class AgentWorkingDirectoryTests(unittest.TestCase):
    """Tests for agent working_directory and session_directory fields."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        self.app = create_app(self.db)
        self.client = TestClient(self.app)
        # Create a test user
        password = "testpass"
        salt = os.urandom(16)
        salt_b64 = base64.b64encode(salt).decode("ascii")
        password_hash = base64.b64encode(hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=2**14,
            r=8,
            p=1,
        )).decode("ascii")
        self.db.insert_user(
            user_id=str(uuid.uuid4()),
            username="testuser",
            password_hash=password_hash,
            salt=salt_b64,
        )
        # Login
        self.client.post(
            "/login",
            data={
                "username": "testuser",
                "password": "testpass"
            },
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_create_agent_with_working_directory(self) -> None:
        """Can create an agent with working_directory set."""
        response = self.client.post(
            "/agents",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "claude",
                "session_mode": "claude",
                "working_directory": "/home/user/project",
                "session_directory": ".claude",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        agents = self.db.list_agents()
        self.assertEqual(len(agents), 1)
        agent = agents[0]
        self.assertEqual(agent.working_directory, "/home/user/project")
        self.assertEqual(agent.session_directory, ".claude")

    def test_update_agent_working_directory(self) -> None:
        """Can update an agent's working_directory."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        response = self.client.post(
            f"/agents/{agent_id}/edit",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "claude",
                "session_mode": "claude",
                "working_directory": "/home/user/myproject",
                "session_directory": ".agent",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        agent = self.db.get_agent(agent_id)
        self.assertEqual(agent.working_directory, "/home/user/myproject")
        self.assertEqual(agent.session_directory, ".agent")

    def test_clear_agent_working_directory(self) -> None:
        """Can clear an agent's working_directory by submitting empty string."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            working_directory="/home/user/project",
            session_directory=".claude",
        )

        # Verify it's set
        agent = self.db.get_agent(agent_id)
        self.assertEqual(agent.working_directory, "/home/user/project")

        # Submit with empty working_directory to clear it
        response = self.client.post(
            f"/agents/{agent_id}/edit",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "claude",
                "session_mode": "claude",
                "working_directory": "", # Empty string should clear
                "session_directory": "", # Empty string should clear
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        # Verify it's cleared
        agent = self.db.get_agent(agent_id)
        self.assertIsNone(agent.working_directory)
        self.assertIsNone(agent.session_directory)

    def test_clone_agent_preserves_working_directory(self) -> None:
        """Cloning an agent preserves working_directory and session_directory."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Original Agent",
            slug="original-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            working_directory="/home/user/project",
            session_directory=".claude",
        )

        response = self.client.post(
            "/ui/agents/bulk-action",
            data={
                "action": "clone",
                "ids": [agent_id]
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)

        agents = self.db.list_agents()
        self.assertEqual(len(agents), 2)

        cloned = [a for a in agents if a.id != agent_id][0]
        self.assertEqual(cloned.working_directory, "/home/user/project")
        self.assertEqual(cloned.session_directory, ".claude")

    def test_agent_edit_page_shows_fields(self) -> None:
        """Agent edit page shows working_directory and session_directory fields."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="claude",
            session_mode="claude",
            vm_target_id=None,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            working_directory="/home/user/project",
            session_directory=".claude",
        )

        response = self.client.get(f"/ui/agents/{agent_id}/edit")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Working Directory", response.text)
        self.assertIn("Session Directory", response.text)
        self.assertIn("/home/user/project", response.text)
        self.assertIn(".claude", response.text)


if __name__ == "__main__":
    unittest.main()
