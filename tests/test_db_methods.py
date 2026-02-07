"""Tests for database operations using Django ORM directly."""

import uuid

import pytest
from asgiref.sync import async_to_sync

from wintermute.models import Agent, Channel, Comment, SupervisorState
from wintermute.utils import utc_now


@pytest.mark.django_db
class TestInsertComment:
    """Tests for Comment creation."""

    def test_create_comment_with_explicit_id(self):
        """Comment should use the provided id."""
        comment_id = str(uuid.uuid4())
        comment = Comment.objects.create(
            id=comment_id,
            body="Test comment body",
            public=1,
            approved=0,
            sent=0,
            created_at=utc_now(),
        )

        assert comment.id == comment_id
        assert comment.body == "Test comment body"
        assert comment.public == 1
        assert comment.approved == 0

    def test_create_comment_with_all_params(self):
        """Comment should accept all optional parameters."""
        comment_id = str(uuid.uuid4())
        comment = Comment.objects.create(
            id=comment_id,
            body="Full test comment",
            public=1,
            approved=1,
            sent=0,
            ticket_id="ticket-123",
            session_id="session-456",
            project_id="project-789",
            agent_id="agent-abc",
            agent_session_id="agent-session-def",
            author="test-author",
            source_id="github",
            issue_number=42,
            origin="api",
            created_at=utc_now(),
        )

        assert comment.body == "Full test comment"
        assert comment.public == 1
        assert comment.approved == 1
        assert comment.sent == 0
        assert comment.ticket_id == "ticket-123"
        assert comment.session_id == "session-456"
        assert comment.project_id == "project-789"
        assert comment.agent_id == "agent-abc"
        assert comment.agent_session_id == "agent-session-def"
        assert comment.author == "test-author"
        assert comment.source_id == "github"
        assert comment.issue_number == 42
        assert comment.origin == "api"


@pytest.mark.django_db
class TestSupervisorState:
    """Tests for SupervisorState upsert operations."""

    def test_create_new_state(self):
        """Should create new state if none exists."""
        state_id = str(uuid.uuid4())
        now = utc_now()
        state = SupervisorState.objects.create(
            id=state_id,
            status="running",
            current_work_id="work-123",
            last_action="polled sources",
            queue_depth=5,
            updated_at=now,
        )

        assert state.status == "running"
        assert state.current_work_id == "work-123"
        assert state.last_action == "polled sources"
        assert state.queue_depth == 5

    def test_update_existing_state(self):
        """Should update existing state."""
        state_id = str(uuid.uuid4())
        now = utc_now()

        # Create initial state
        state = SupervisorState.objects.create(
            id=state_id,
            status="starting",
            current_work_id=None,
            last_action="initializing",
            queue_depth=0,
            updated_at=now,
        )

        # Update state
        state.status = "running"
        state.current_work_id = "work-456"
        state.last_action = "processing"
        state.queue_depth = 3
        state.save()

        # Refresh and verify
        state.refresh_from_db()
        assert state.status == "running"
        assert state.current_work_id == "work-456"
        assert state.last_action == "processing"
        assert state.queue_depth == 3


@pytest.mark.django_db
class TestChannels:
    """Tests for Channel operations."""

    def test_list_channels_for_agent(self):
        """Should list channels for a specific agent."""
        # Create an agent
        agent = Agent.objects.create(
            id="agent-1",
            name="Test Agent",
            slug="test-agent",
            command="echo test",
            session_mode="tmux",
        )

        # Add channels
        Channel.objects.create(
            id="ch-1",
            agent_id=agent.id,
            type="slack",
            name="test-channel",
            external_channel_id="C123",
            enabled=True,
        )

        # Query channels
        channels = list(Channel.objects.filter(agent_id="agent-1"))
        assert len(channels) == 1
        assert channels[0].name == "test-channel"


@pytest.mark.django_db(transaction=True)
class TestAsyncOperations:
    """Tests for async database operations."""

    def test_create_comment_async(self):
        """Should support async comment creation."""
        from asgiref.sync import sync_to_async

        async def run_test():
            comment_id = str(uuid.uuid4())
            create_comment = sync_to_async(Comment.objects.create)
            comment = await create_comment(
                id=comment_id,
                body="Async comment test",
                public=1,
                approved=0,
                sent=0,
                created_at=utc_now(),
            )
            assert comment.id == comment_id
            assert comment.body == "Async comment test"

        async_to_sync(run_test)()
