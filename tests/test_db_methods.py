"""Tests for Database methods - insert_comment, upsert_supervisor_state, and AsyncDatabase."""

import uuid
from django.test import TestCase, TransactionTestCase
from asgiref.sync import sync_to_async, async_to_sync

from wintermute.db import AsyncDatabase, Database
from wintermute.models import Agent, Channel, Comment, SupervisorState


class InsertCommentTests(TestCase):
    """Tests for Database.insert_comment method."""

    def setUp(self) -> None:
        # Django TestCase uses test database automatically
        self.db = Database(":memory:") # Path ignored, uses Django's test DB

    def test_insert_comment_with_explicit_id(self) -> None:
        """insert_comment should use the provided comment_id."""
        comment_id = str(uuid.uuid4())
        comment = self.db.insert_comment(
            comment_id=comment_id,
            body="Test comment body",
            public=True,
            approved=False,
        )

        self.assertEqual(comment.id, comment_id)
        self.assertEqual(comment.body, "Test comment body")
        self.assertEqual(comment.public, 1) # bool True -> int 1
        self.assertEqual(comment.approved, 0) # bool False -> int 0

    def test_insert_comment_with_all_params(self) -> None:
        """insert_comment should accept all optional parameters."""
        comment_id = str(uuid.uuid4())
        comment = self.db.insert_comment(
            comment_id=comment_id,
            body="Full test comment",
            public=True,
            approved=True,
            sent=False,
            ticket_id="ticket-123",
            session_id="session-456",
            project_id="project-789",
            agent_id="agent-abc",
            agent_session_id="agent-session-def",
            author="test-author",
            source_id="github",
            issue_number=42,
            origin="api",
        )

        self.assertEqual(comment.body, "Full test comment")
        self.assertEqual(comment.public, 1)
        self.assertEqual(comment.approved, 1)
        self.assertEqual(comment.sent, 0)
        self.assertEqual(comment.ticket_id, "ticket-123")
        self.assertEqual(comment.session_id, "session-456")
        self.assertEqual(comment.project_id, "project-789")
        self.assertEqual(comment.agent_id, "agent-abc")
        self.assertEqual(comment.agent_session_id, "agent-session-def")
        self.assertEqual(comment.author, "test-author")
        self.assertEqual(comment.source_id, "github")
        self.assertEqual(comment.issue_number, 42)
        self.assertEqual(comment.origin, "api")

    def test_insert_comment_bool_conversion(self) -> None:
        """insert_comment should convert bool to int correctly."""
        comment_id = str(uuid.uuid4())

        # Test True -> 1
        comment_true = self.db.insert_comment(
            comment_id=comment_id,
            body="True test",
            public=True,
            approved=True,
            sent=True,
        )
        self.assertEqual(comment_true.public, 1)
        self.assertEqual(comment_true.approved, 1)
        self.assertEqual(comment_true.sent, 1)

        # Test False -> 0
        comment_id2 = str(uuid.uuid4())
        comment_false = self.db.insert_comment(
            comment_id=comment_id2,
            body="False test",
            public=False,
            approved=False,
            sent=False,
        )
        self.assertEqual(comment_false.public, 0)
        self.assertEqual(comment_false.approved, 0)
        self.assertEqual(comment_false.sent, 0)


class UpsertSupervisorStateTests(TestCase):
    """Tests for Database.upsert_supervisor_state method."""

    def setUp(self) -> None:
        self.db = Database(":memory:")

    def test_upsert_creates_new_state(self) -> None:
        """upsert_supervisor_state should create new state if none exists."""
        state = self.db.upsert_supervisor_state(
            status="running",
            current_work_id="work-123",
            last_action="polled sources",
            queue_depth=5,
        )

        self.assertEqual(state.status, "running")
        self.assertEqual(state.current_work_id, "work-123")
        self.assertEqual(state.last_action, "polled sources")
        self.assertEqual(state.queue_depth, 5)
        self.assertIsNotNone(state.updated_at)

    def test_upsert_updates_existing_state(self) -> None:
        """upsert_supervisor_state should update existing state."""
        # Create initial state
        state1 = self.db.upsert_supervisor_state(
            status="starting",
            current_work_id=None,
            last_action="initializing",
            queue_depth=0,
        )
        state1_id = state1.id

        # Update state
        state2 = self.db.upsert_supervisor_state(
            status="running",
            current_work_id="work-456",
            last_action="processing",
            queue_depth=3,
        )

        # Should be the same record
        self.assertEqual(state2.id, state1_id)
        self.assertEqual(state2.status, "running")
        self.assertEqual(state2.current_work_id, "work-456")
        self.assertEqual(state2.last_action, "processing")
        self.assertEqual(state2.queue_depth, 3)

    def test_upsert_without_created_at(self) -> None:
        """upsert_supervisor_state should not require created_at field."""
        # This should not raise TypeError about created_at
        state = self.db.upsert_supervisor_state(
            status="stopped",
            current_work_id=None,
            last_action="shutdown",
            queue_depth=0,
        )
        self.assertEqual(state.status, "stopped")


class AsyncDatabaseTests(TransactionTestCase):
    """Tests for AsyncDatabase wrapper."""

    def setUp(self) -> None:
        self.db = AsyncDatabase(":memory:")

    def test_insert_comment_async(self) -> None:
        """AsyncDatabase should support insert_comment via __getattr__."""

        async def run_test():
            comment_id = str(uuid.uuid4())
            comment = await self.db.insert_comment(
                comment_id=comment_id,
                body="Async comment test",
                public=True,
                approved=False,
            )

            self.assertEqual(comment.id, comment_id)
            self.assertEqual(comment.body, "Async comment test")
            self.assertEqual(comment.public, 1)

        async_to_sync(run_test)()

    def test_upsert_supervisor_state_async(self) -> None:
        """AsyncDatabase should support upsert_supervisor_state."""

        async def run_test():
            state = await self.db.upsert_supervisor_state(
                status="running",
                current_work_id="work-async",
                last_action="async test",
                queue_depth=2,
            )

            self.assertEqual(state.status, "running")
            self.assertEqual(state.current_work_id, "work-async")

        async_to_sync(run_test)()

    def test_list_channels_async(self) -> None:
        """AsyncDatabase.list_channels should be awaitable."""

        async def run_test():
            # Create an agent directly via Django ORM
            agent = await sync_to_async(Agent.objects.create)(
                id="agent-1",
                name="Test Agent",
                slug="test-agent",
                command="echo test",
                session_mode="tmux",
            )

            # Add a channel
            await sync_to_async(Channel.objects.create)(
                id="ch-1",
                agent=agent,
                type="slack",
                name="test-channel",
                external_channel_id="C123",
                enabled=True,
            )

            # list_channels should be awaitable and return a list
            channels = await self.db.list_channels(agent_id="agent-1")
            self.assertIsInstance(channels, list)
            self.assertEqual(len(channels), 1)
            self.assertEqual(channels[0].name, "test-channel")

        async_to_sync(run_test)()


if __name__ == "__main__":
    import unittest
    unittest.main()
