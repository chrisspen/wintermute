"""Unit tests for WebSocket comment streaming endpoints."""

import base64
import hashlib
import json
import os
import tempfile
import time
import unittest
import uuid
from urllib.parse import quote

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


class TicketWebSocketTests(unittest.TestCase):
    """Tests for ticket comment WebSocket endpoint."""

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

        # Create a test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create a test ticket
        self.ticket_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket_id,
            project_id=self.project_id,
            title="Test Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_websocket_connect(self) -> None:
        """Test that WebSocket connection is accepted."""
        with self.client.websocket_connect(f"/ws/tickets/{self.ticket_id}") as ws:
            # Should not raise an exception
            pass

    def test_websocket_receives_existing_comments(self) -> None:
        """Test that WebSocket receives existing comments on connect."""
        # Insert a comment before connecting
        comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=None,
            author="user",
            source_id=None,
            issue_number=None,
            body="Test comment body",
            public=False,
            approved=False,
            origin="web",
        )

        with self.client.websocket_connect(f"/ws/tickets/{self.ticket_id}") as ws:
            # Should receive the existing comment
            data = ws.receive_json()
            self.assertEqual(data["type"], "comment")
            self.assertEqual(data["data"]["id"], comment_id)
            self.assertEqual(data["data"]["body"], "Test comment body")

    def test_websocket_since_filter(self) -> None:
        """Test that 'since' parameter filters comments."""
        # Insert an old comment
        old_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=old_comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=None,
            author="user",
            source_id=None,
            issue_number=None,
            body="Old comment",
            public=False,
            approved=False,
            origin="web",
        )
        # Get the timestamp of this comment
        old_comment = self.db.get_comment(old_comment_id)
        old_ts = old_comment.created_at

        # Wait to ensure timestamp difference (SQLite datetime precision)
        time.sleep(1.1)

        # Insert a new comment
        new_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=new_comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=None,
            author="user",
            source_id=None,
            issue_number=None,
            body="New comment",
            public=False,
            approved=False,
            origin="web",
        )

        # Connect with 'since' filter (URL-encode the timestamp)
        with self.client.websocket_connect(
            f"/ws/tickets/{self.ticket_id}?since={quote(old_ts, safe='')}"
        ) as ws:
            # Should only receive the new comment
            data = ws.receive_json()
            self.assertEqual(data["type"], "comment")
            self.assertEqual(data["data"]["id"], new_comment_id)

    def test_websocket_nonexistent_ticket(self) -> None:
        """Test WebSocket for non-existent ticket still connects (empty stream)."""
        # The WebSocket handler doesn't reject for non-existent tickets,
        # it just returns an empty stream
        with self.client.websocket_connect("/ws/tickets/nonexistent-id") as ws:
            # Connection should succeed (no comments to receive)
            pass


class AgentWebSocketTests(unittest.TestCase):
    """Tests for agent comment WebSocket endpoint."""

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

    def test_websocket_connect(self) -> None:
        """Test that WebSocket connection is accepted."""
        with self.client.websocket_connect(
            f"/ws/agents/{self.agent_id}/comments"
        ) as ws:
            # Should not raise an exception
            pass

    def test_websocket_receives_existing_comments(self) -> None:
        """Test that WebSocket receives existing comments for agent session."""
        # Create a standalone session
        session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=session_id,
            project_id=None,
            agent_id=self.agent_id,
            ticket_id=None,
            status="stopped",
            repo_path="",
            thread_ts=None,
        )

        # Insert a comment for this agent session
        comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=comment_id,
            ticket_id=None,
            session_id=None,
            project_id=None,
            agent_id=self.agent_id,
            author="agent",
            source_id=None,
            issue_number=None,
            body="Agent response",
            public=False,
            approved=False,
            agent_session_id=session_id,
            origin="session",
        )

        with self.client.websocket_connect(
            f"/ws/agents/{self.agent_id}/comments"
        ) as ws:
            # Should receive the existing comment
            data = ws.receive_json()
            self.assertEqual(data["type"], "comment")
            self.assertEqual(data["data"]["id"], comment_id)
            self.assertEqual(data["data"]["body"], "Agent response")

    def test_websocket_multiple_sessions_gets_latest(self) -> None:
        """Test WebSocket gets comments from most recent session."""
        # Create an old session (first)
        old_session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=old_session_id,
            project_id=None,
            agent_id=self.agent_id,
            ticket_id=None,
            status="stopped",
            repo_path="",
            thread_ts=None,
        )
        old_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=old_comment_id,
            ticket_id=None,
            session_id=None,
            project_id=None,
            agent_id=self.agent_id,
            author="agent",
            source_id=None,
            issue_number=None,
            body="Old session response",
            public=False,
            approved=False,
            agent_session_id=old_session_id,
            origin="session",
        )

        # Small delay to ensure session order
        time.sleep(0.1)

        # Create a newer session (second, created after old)
        new_session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=new_session_id,
            project_id=None,
            agent_id=self.agent_id,
            ticket_id=None,
            status="stopped",
            repo_path="",
            thread_ts=None,
        )
        new_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=new_comment_id,
            ticket_id=None,
            session_id=None,
            project_id=None,
            agent_id=self.agent_id,
            author="agent",
            source_id=None,
            issue_number=None,
            body="New session response",
            public=False,
            approved=False,
            agent_session_id=new_session_id,
            origin="session",
        )

        with self.client.websocket_connect(
            f"/ws/agents/{self.agent_id}/comments"
        ) as ws:
            # Should receive comment from newer session
            data = ws.receive_json()
            self.assertEqual(data["type"], "comment")
            self.assertEqual(data["data"]["id"], new_comment_id)
            self.assertEqual(data["data"]["body"], "New session response")

    def test_websocket_since_filter(self) -> None:
        """Test that 'since' parameter filters comments."""
        # Create session
        session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=session_id,
            project_id=None,
            agent_id=self.agent_id,
            ticket_id=None,
            status="stopped",
            repo_path="",
            thread_ts=None,
        )

        # Insert an old comment
        old_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=old_comment_id,
            ticket_id=None,
            session_id=None,
            project_id=None,
            agent_id=self.agent_id,
            author="user",
            source_id=None,
            issue_number=None,
            body="Old comment",
            public=False,
            approved=False,
            agent_session_id=session_id,
            origin="web",
        )
        old_comment = self.db.get_comment(old_comment_id)
        old_ts = old_comment.created_at

        # Wait to ensure timestamp difference (SQLite datetime precision)
        time.sleep(1.1)

        # Insert a new comment
        new_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=new_comment_id,
            ticket_id=None,
            session_id=None,
            project_id=None,
            agent_id=self.agent_id,
            author="agent",
            source_id=None,
            issue_number=None,
            body="New comment",
            public=False,
            approved=False,
            agent_session_id=session_id,
            origin="session",
        )

        with self.client.websocket_connect(
            f"/ws/agents/{self.agent_id}/comments?since={quote(old_ts, safe='')}"
        ) as ws:
            # Should only receive the new comment
            data = ws.receive_json()
            self.assertEqual(data["type"], "comment")
            self.assertEqual(data["data"]["id"], new_comment_id)


class CommentStreamAPITests(unittest.TestCase):
    """Tests for comment-related API endpoints used by WebSocket clients."""

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

        # Create a test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create a test ticket
        self.ticket_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket_id,
            project_id=self.project_id,
            title="Test Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

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

    def test_add_ticket_comment(self) -> None:
        """Test adding a comment to a ticket via API."""
        response = self.client.post(
            f"/api/tickets/{self.ticket_id}/comments",
            json={"body": "Test comment", "public": False},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("comment", data)
        self.assertEqual(data["comment"]["body"], "Test comment")
        self.assertEqual(data["comment"]["author"], "testuser")  # Uses logged-in username

    def test_add_agent_comment(self) -> None:
        """Test adding a comment to an agent via API."""
        # Create a session first
        session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=session_id,
            project_id=None,
            agent_id=self.agent_id,
            ticket_id=None,
            status="running",
            repo_path="",
            thread_ts=None,
        )
        self.db.update_session(session_id, queued_user_messages="[]")

        response = self.client.post(
            f"/api/agents/{self.agent_id}/comments",
            json={"body": "User message to agent"},
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("comment", data)
        self.assertEqual(data["comment"]["body"], "User message to agent")
        self.assertEqual(data["comment"]["author"], "user")  # Agent endpoint uses 'user'
        # Verify the comment was stored in the database with the session association
        comment_id = data["comment"]["id"]
        stored_comment = self.db.get_comment(comment_id)
        self.assertEqual(stored_comment.agent_session_id, session_id)

    def test_add_agent_comment_queues_message(self) -> None:
        """Test that adding comment to running agent queues the message."""
        # Create a running session
        session_id = str(uuid.uuid4())
        self.db.insert_session(
            session_id=session_id,
            project_id=None,
            agent_id=self.agent_id,
            ticket_id=None,
            status="running",
            repo_path="",
            thread_ts=None,
        )
        self.db.update_session(session_id, queued_user_messages="[]")

        response = self.client.post(
            f"/api/agents/{self.agent_id}/comments",
            json={"body": "Message to queue"},
        )
        self.assertEqual(response.status_code, 200)

        # Check that message was queued (queue items are plain strings)
        session = self.db.get_session(session_id)
        queue = json.loads(session.queued_user_messages or "[]")
        self.assertEqual(len(queue), 1)
        self.assertIn("Message to queue", queue[0])


if __name__ == "__main__":
    unittest.main()
