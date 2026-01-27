"""Unit tests for agent wake system."""

import base64
import hashlib
import os
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from wintermute.db import Database, AgentWakeRecord
from wintermute.sources.sessions import (
    _parse_wake_commands,
    _has_clear_wakes_command,
    _strip_wake_commands,
    MAX_WAKE_DURATION_SECONDS,
)
from wintermute.web.app import create_app


class WakeCommandParsingTests(unittest.TestCase):
    """Tests for parsing /wakeme and /clear-wakes commands."""

    def test_parse_wakeme_seconds(self) -> None:
        """Test parsing /wakeme with seconds."""
        output = "/wakeme 30s"
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], 30)
        self.assertIsNone(wakes[0][1])

    def test_parse_wakeme_minutes(self) -> None:
        """Test parsing /wakeme with minutes."""
        output = "/wakeme 5m"
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], 300)
        self.assertIsNone(wakes[0][1])

    def test_parse_wakeme_hours(self) -> None:
        """Test parsing /wakeme with hours."""
        output = "/wakeme 2h"
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], 7200)
        self.assertIsNone(wakes[0][1])

    def test_parse_wakeme_days(self) -> None:
        """Test parsing /wakeme with days."""
        output = "/wakeme 1d"
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], 86400)
        self.assertIsNone(wakes[0][1])

    def test_parse_wakeme_with_context(self) -> None:
        """Test parsing /wakeme with context string."""
        output = '/wakeme 5m "check pipeline status"'
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], 300)
        self.assertEqual(wakes[0][1], "check pipeline status")

    def test_parse_wakeme_max_duration(self) -> None:
        """Test that duration is capped at max."""
        output = "/wakeme 48h" # More than 24h max
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], MAX_WAKE_DURATION_SECONDS)

    def test_parse_multiple_wakeme(self) -> None:
        """Test parsing multiple /wakeme commands."""
        output = """/wakeme 5m "first"
some other text
/wakeme 10m "second"
"""
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 2)
        self.assertEqual(wakes[0][0], 300)
        self.assertEqual(wakes[0][1], "first")
        self.assertEqual(wakes[1][0], 600)
        self.assertEqual(wakes[1][1], "second")

    def test_parse_wakeme_case_insensitive(self) -> None:
        """Test that /wakeme is case insensitive."""
        output = "/WAKEME 5M"
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0][0], 300)

    def test_parse_wakeme_no_match(self) -> None:
        """Test that non-matching text is ignored."""
        output = "Just some regular text"
        wakes = _parse_wake_commands(output)
        self.assertEqual(len(wakes), 0)

    def test_has_clear_wakes_command(self) -> None:
        """Test detecting /clear-wakes command."""
        self.assertTrue(_has_clear_wakes_command("/clear-wakes"))
        self.assertTrue(_has_clear_wakes_command("/clear-wakes\n"))
        self.assertTrue(_has_clear_wakes_command("text\n/clear-wakes\nmore"))
        self.assertTrue(_has_clear_wakes_command("/CLEAR-WAKES"))

    def test_has_clear_wakes_command_no_match(self) -> None:
        """Test that non-matching text returns False."""
        self.assertFalse(_has_clear_wakes_command("no command here"))
        self.assertFalse(_has_clear_wakes_command("/clear-wakes-extra"))

    def test_strip_wake_commands(self) -> None:
        """Test stripping wake commands from output."""
        output = 'Some text\n/wakeme 5m "context"\nMore text\n/clear-wakes\nEnd'
        stripped = _strip_wake_commands(output)
        self.assertNotIn("/wakeme", stripped)
        self.assertNotIn("/clear-wakes", stripped)
        self.assertIn("Some text", stripped)
        self.assertIn("More text", stripped)
        self.assertIn("End", stripped)


class AgentWakeDatabaseTests(unittest.TestCase):
    """Tests for agent wake database operations."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create test VM target
        self.vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create test agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        # Create test session
        self.session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=self.session_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            ticket_id=None,
            status="running",
            repo_path="/tmp/test",
            thread_ts=None,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_insert_agent_wake(self) -> None:
        """Test inserting an agent wake."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
            context="Test context",
        )
        wake = self.db.get_agent_wake(wake_id)
        self.assertIsNotNone(wake)
        self.assertEqual(wake.id, wake_id)
        self.assertEqual(wake.agent_session_id, self.session_id)
        self.assertEqual(wake.duration_seconds, 300)
        self.assertEqual(wake.context, "Test context")
        self.assertEqual(wake.status, "pending")
        self.assertIsNone(wake.fired_at)
        self.assertIsNone(wake.cancelled_at)

    def test_list_agent_wakes(self) -> None:
        """Test listing agent wakes."""
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=10)).isoformat(),
            duration_seconds=600,
        )
        wakes = self.db.list_agent_wakes()
        self.assertEqual(len(wakes), 2)

    def test_list_agent_wakes_by_status(self) -> None:
        """Test filtering wakes by status."""
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=10)).isoformat(),
            duration_seconds=600,
        )
        self.db.cancel_agent_wake(wake_id1, "user")
        pending = self.db.list_agent_wakes(status="pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, wake_id2)
        cancelled = self.db.list_agent_wakes(status="cancelled")
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0].id, wake_id1)

    def test_list_agent_wakes_by_session(self) -> None:
        """Test filtering wakes by session."""
        other_session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=other_session_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            status="running",
            repo_path="/tmp/other",
            ticket_id=None,
            thread_ts=None,
        )
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=other_session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        wakes = self.db.list_agent_wakes(agent_session_id=self.session_id)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0].id, wake_id1)

    def test_get_pending_agent_wakes(self) -> None:
        """Test getting pending wakes before a time."""
        now = datetime.now(timezone.utc)
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        # Wake in the past
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now - timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        # Wake in the future
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        pending = self.db.get_pending_agent_wakes(before=now.isoformat())
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].id, wake_id1)

    def test_fire_agent_wake(self) -> None:
        """Test firing an agent wake."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
        )
        result = self.db.fire_agent_wake(wake_id)
        self.assertTrue(result)
        wake = self.db.get_agent_wake(wake_id)
        self.assertEqual(wake.status, "fired")
        self.assertIsNotNone(wake.fired_at)

    def test_fire_agent_wake_not_pending(self) -> None:
        """Test that firing non-pending wake fails."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
        )
        self.db.cancel_agent_wake(wake_id, "user")
        result = self.db.fire_agent_wake(wake_id)
        self.assertFalse(result)
        wake = self.db.get_agent_wake(wake_id)
        self.assertEqual(wake.status, "cancelled")

    def test_cancel_agent_wake(self) -> None:
        """Test cancelling an agent wake."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
        )
        result = self.db.cancel_agent_wake(wake_id, "user")
        self.assertTrue(result)
        wake = self.db.get_agent_wake(wake_id)
        self.assertEqual(wake.status, "cancelled")
        self.assertIsNotNone(wake.cancelled_at)
        self.assertEqual(wake.cancelled_by, "user")

    def test_cancel_agent_wakes_for_session(self) -> None:
        """Test cancelling all pending wakes for a session."""
        now = datetime.now(timezone.utc)
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        wake_id3 = str(uuid.uuid4())
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=10)).isoformat(),
            duration_seconds=600,
        )
        # Third wake is already fired
        self.db.insert_agent_wake(
            wake_id=wake_id3,
            agent_session_id=self.session_id,
            wake_at=(now - timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.fire_agent_wake(wake_id3)

        count = self.db.cancel_agent_wakes_for_session(self.session_id, "agent")
        self.assertEqual(count, 2)

        wake1 = self.db.get_agent_wake(wake_id1)
        wake2 = self.db.get_agent_wake(wake_id2)
        wake3 = self.db.get_agent_wake(wake_id3)
        self.assertEqual(wake1.status, "cancelled")
        self.assertEqual(wake1.cancelled_by, "agent")
        self.assertEqual(wake2.status, "cancelled")
        self.assertEqual(wake3.status, "fired") # Should not be cancelled


class AgentWakeUITests(unittest.TestCase):
    """Tests for agent wake UI endpoints."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)

        # Create test user with properly hashed password
        self.user_id = str(uuid.uuid4())
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
            user_id=self.user_id,
            username="testuser",
            password_hash=password_hash,
            salt=salt_b64,
        )
        # Login to get session cookie
        self.client.post(
            "/login",
            data={
                "username": "testuser",
                "password": "testpass"
            },
            follow_redirects=False,
        )

        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create test VM target
        self.vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create test agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        # Create test session
        self.session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=self.session_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            status="running",
            repo_path="/tmp/test",
            ticket_id=None,
            thread_ts=None,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_agent_wakes_list_page(self) -> None:
        """Test agent wakes list page loads."""
        response = self.client.get("/ui/agent-wakes")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Agent Wakes", response.text)

    def test_agent_wakes_list_shows_wakes(self) -> None:
        """Test agent wakes list shows wakes."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
            context="Test wake context",
        )
        response = self.client.get("/ui/agent-wakes")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Test wake context", response.text)

    def test_agent_wakes_filter_by_status(self) -> None:
        """Test filtering wakes by status."""
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
            context="Pending wake",
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=10)).isoformat(),
            duration_seconds=600,
            context="Cancelled wake",
        )
        self.db.cancel_agent_wake(wake_id2, "user")

        # Filter by pending
        response = self.client.get("/ui/agent-wakes?status=pending")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Pending wake", response.text)
        self.assertNotIn("Cancelled wake", response.text)

        # Filter by cancelled
        response = self.client.get("/ui/agent-wakes?status=cancelled")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Pending wake", response.text)
        self.assertIn("Cancelled wake", response.text)

    def test_agent_wake_detail_page(self) -> None:
        """Test agent wake detail page."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
            context="Detail test context",
        )
        response = self.client.get(f"/ui/agent-wakes/{wake_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Detail test context", response.text)
        self.assertIn("5m", response.text) # Duration label

    def test_agent_wake_detail_not_found(self) -> None:
        """Test agent wake detail 404."""
        response = self.client.get(f"/ui/agent-wakes/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_cancel_agent_wake_from_ui(self) -> None:
        """Test cancelling a wake from UI."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
        )
        response = self.client.post(
            f"/agent-wakes/{wake_id}/cancel",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        wake = self.db.get_agent_wake(wake_id)
        self.assertEqual(wake.status, "cancelled")
        self.assertEqual(wake.cancelled_by, "user")


class AgentWakeAPITests(unittest.TestCase):
    """Tests for agent wake API endpoints."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)

        # Create test user with properly hashed password
        self.user_id = str(uuid.uuid4())
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
            user_id=self.user_id,
            username="testuser",
            password_hash=password_hash,
            salt=salt_b64,
        )
        # Login to get session cookie
        self.client.post(
            "/login",
            data={
                "username": "testuser",
                "password": "testpass"
            },
            follow_redirects=False,
        )

        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create test VM target
        self.vm_id = str(uuid.uuid4())
        self.db.insert_vm_target(
            vm_id=self.vm_id,
            name="test-vm",
            host="localhost",
            user="testuser",
            port=22,
        )

        # Create test agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
            vm_target_id=self.vm_id,
            required_ssh_options=None,
            env_vars=None,
            mcp_config=None,
            trust_level=None,
            input_echo_prefix=None,
            response_prefix=None,
        )

        # Create test session
        self.session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=self.session_id,
            project_id=self.project_id,
            agent_id=self.agent_id,
            status="running",
            repo_path="/tmp/test",
            ticket_id=None,
            thread_ts=None,
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_api_list_agent_wakes(self) -> None:
        """Test API list agent wakes."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
            context="API test",
        )
        response = self.client.get("/api/agent-wakes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["wakes"]), 1)
        self.assertEqual(data["wakes"][0]["id"], wake_id)
        self.assertEqual(data["wakes"][0]["context"], "API test")

    def test_api_list_agent_wakes_filter_status(self) -> None:
        """Test API list wakes with status filter."""
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=10)).isoformat(),
            duration_seconds=600,
        )
        self.db.cancel_agent_wake(wake_id2, "user")

        response = self.client.get("/api/agent-wakes?status=pending")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["wakes"]), 1)
        self.assertEqual(data["wakes"][0]["id"], wake_id1)

    def test_api_get_agent_wake(self) -> None:
        """Test API get single agent wake."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
            context="Get API test",
        )
        response = self.client.get(f"/api/agent-wakes/{wake_id}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["id"], wake_id)
        self.assertEqual(data["context"], "Get API test")
        self.assertEqual(data["duration_seconds"], 300)
        self.assertEqual(data["status"], "pending")

    def test_api_get_agent_wake_not_found(self) -> None:
        """Test API get agent wake 404."""
        response = self.client.get(f"/api/agent-wakes/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_api_cancel_agent_wake(self) -> None:
        """Test API cancel agent wake."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
        )
        response = self.client.post(f"/api/agent-wakes/{wake_id}/cancel")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        wake = self.db.get_agent_wake(wake_id)
        self.assertEqual(wake.status, "cancelled")

    def test_api_cancel_agent_wake_not_pending(self) -> None:
        """Test API cancel fails for non-pending wake."""
        wake_id = str(uuid.uuid4())
        wake_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        self.db.insert_agent_wake(
            wake_id=wake_id,
            agent_session_id=self.session_id,
            wake_at=wake_at,
            duration_seconds=300,
        )
        self.db.fire_agent_wake(wake_id)
        response = self.client.post(f"/api/agent-wakes/{wake_id}/cancel")
        self.assertEqual(response.status_code, 400)

    def test_api_clear_session_wakes(self) -> None:
        """Test API clear all wakes for a session."""
        wake_id1 = str(uuid.uuid4())
        wake_id2 = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        self.db.insert_agent_wake(
            wake_id=wake_id1,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=5)).isoformat(),
            duration_seconds=300,
        )
        self.db.insert_agent_wake(
            wake_id=wake_id2,
            agent_session_id=self.session_id,
            wake_at=(now + timedelta(minutes=10)).isoformat(),
            duration_seconds=600,
        )
        response = self.client.post(f"/api/sessions/{self.session_id}/clear-wakes")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["cancelled"], 2)

        wake1 = self.db.get_agent_wake(wake_id1)
        wake2 = self.db.get_agent_wake(wake_id2)
        self.assertEqual(wake1.status, "cancelled")
        self.assertEqual(wake2.status, "cancelled")


if __name__ == "__main__":
    unittest.main()
