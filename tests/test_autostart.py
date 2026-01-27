"""Tests for agent autostart feature."""

import base64
import hashlib
import os
import tempfile
import unittest
import uuid
from unittest.mock import patch, MagicMock, AsyncMock

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.sources.autostart import AutostartSource, AutostartWorkItem
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


class AutostartModelTests(unittest.TestCase):
    """Tests for autostart field in Agent model."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_agent_with_autostart_true(self) -> None:
        """Test inserting agent with autostart=True."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
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
            autostart=True,
        )
        agent = self.db.get_agent(agent_id)
        self.assertIsNotNone(agent)
        self.assertTrue(agent.autostart)

    def test_insert_agent_with_autostart_false(self) -> None:
        """Test inserting agent with autostart=False (default)."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
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
        agent = self.db.get_agent(agent_id)
        self.assertIsNotNone(agent)
        self.assertFalse(agent.autostart)

    def test_update_agent_autostart(self) -> None:
        """Test updating agent autostart field."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
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
            autostart=False,
        )
        self.db.update_agent(agent_id, autostart=True)
        agent = self.db.get_agent(agent_id)
        self.assertTrue(agent.autostart)

        self.db.update_agent(agent_id, autostart=False)
        agent = self.db.get_agent(agent_id)
        self.assertFalse(agent.autostart)

    def test_list_agents_includes_autostart(self) -> None:
        """Test that list_agents includes autostart field."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
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
            autostart=True,
        )
        agents = self.db.list_agents()
        self.assertEqual(len(agents), 1)
        self.assertTrue(agents[0].autostart)


class AutostartWebTests(unittest.TestCase):
    """Tests for autostart in web UI."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        self.app = create_app(self.db)
        self.client = TestClient(self.app)
        _create_test_user(self.db)
        self.client.post(
            "/login",
            data={"username": "testuser", "password": "testpass"},
            follow_redirects=False,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_create_agent_with_autostart(self) -> None:
        """Test creating agent with autostart checkbox checked."""
        response = self.client.post(
            "/agents",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "echo test",
                "session_mode": "tmux",
                "autostart": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        agents = self.db.list_agents()
        self.assertEqual(len(agents), 1)
        self.assertTrue(agents[0].autostart)

    def test_create_agent_without_autostart(self) -> None:
        """Test creating agent without autostart checkbox."""
        response = self.client.post(
            "/agents",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "echo test",
                "session_mode": "tmux",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        agents = self.db.list_agents()
        self.assertEqual(len(agents), 1)
        self.assertFalse(agents[0].autostart)

    def test_update_agent_autostart(self) -> None:
        """Test updating agent autostart via form."""
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
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
            autostart=False,
        )

        # Enable autostart
        response = self.client.post(
            f"/agents/{agent_id}/edit",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "echo test",
                "session_mode": "tmux",
                "autostart": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        agent = self.db.get_agent(agent_id)
        self.assertTrue(agent.autostart)

        # Disable autostart (checkbox not submitted)
        response = self.client.post(
            f"/agents/{agent_id}/edit",
            data={
                "name": "Test Agent",
                "slug": "test-agent",
                "command": "echo test",
                "session_mode": "tmux",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        agent = self.db.get_agent(agent_id)
        self.assertFalse(agent.autostart)


class AutostartSourceTests(unittest.TestCase):
    """Tests for AutostartSource."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        self.source = AutostartSource()

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_poll_no_autostart_agents(self) -> None:
        """Test poll returns empty when no agents have autostart enabled."""
        import asyncio
        # Create agent without autostart
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
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
            autostart=False,
        )
        drafts = asyncio.run(self.source.poll({"db": self.db}))
        self.assertEqual(len(drafts), 0)

    def test_poll_autostart_agent_without_vm(self) -> None:
        """Test poll skips autostart agents without VM target."""
        import asyncio
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=None,  # No VM target
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            autostart=True,
        )
        drafts = asyncio.run(self.source.poll({"db": self.db}))
        self.assertEqual(len(drafts), 0)

    def test_poll_autostart_agent_with_vm(self) -> None:
        """Test poll creates draft for autostart agent with VM target."""
        import asyncio
        vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=vm_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=vm_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            autostart=True,
        )
        drafts = asyncio.run(self.source.poll({"db": self.db}))
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].checkpoint["agent_id"], agent_id)

    def test_poll_skips_already_running_agent(self) -> None:
        """Test poll skips agents that already have a running session."""
        import asyncio
        vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=vm_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )
        agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=vm_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
            autostart=True,
        )
        # Create a running session (standalone, no ticket)
        session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=session_id,
            project_id=None,
            agent_id=agent_id,
            ticket_id=None,
            status="running",
            repo_path="/tmp/test",
            thread_ts=None,
            workspace_path="/tmp/test",
        )
        drafts = asyncio.run(self.source.poll({"db": self.db}))
        self.assertEqual(len(drafts), 0)


if __name__ == "__main__":
    unittest.main()
