"""Tests for error handling in sources and related code."""

import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import aiohttp

from wintermute.db import Database, ChannelRecord
from wintermute.sources.gitlab import GitLabIssuesSource


class GitLabAPIErrorHandlingTests(unittest.IsolatedAsyncioTestCase):
    """Tests for GitLab source handling API errors gracefully."""

    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        self.db.insert_remote_token(
            token_id="gl-tok-1",
            provider="gitlab",
            user_login="testuser",
            token="glpat_faketoken",
        )
        self.db.insert_project(
            "proj-gl-1", "GitLab Project", "gitlab-proj", None,
            provider="gitlab",
            source_token_id="gl-tok-1",
            source_repo="group/project",
            issue_state="opened",
            source_enabled=True,
            poll_interval_seconds=10,
        )

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_handles_500_html_response(self) -> None:
        """GitLab source should not crash when API returns 500 with HTML error page."""
        source = GitLabIssuesSource()
        ctx = {"db": self.db}

        # Create a mock response that returns HTML (like GitLab error pages)
        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.json = AsyncMock(
            side_effect=aiohttp.ContentTypeError(
                MagicMock(),
                (),
                message="Attempt to decode JSON with unexpected mimetype: text/html"
            )
        )

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.get = MagicMock(return_value=mock_response)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value = mock_session

            # This should NOT raise an exception - should handle gracefully
            try:
                result = await source._fetch_issues(
                    token="test_token",
                    project_id="group/project",
                    state="opened",
                    labels=[],
                )
                # Should return empty list on error
                self.assertEqual(result, [])
            except aiohttp.ContentTypeError:
                self.fail("GitLab source crashed on HTML error response - should handle gracefully")

    async def test_handles_400_error(self) -> None:
        """GitLab source should return empty list on 400 errors."""
        source = GitLabIssuesSource()

        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.json = AsyncMock(return_value={"error": "Bad request"})

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=None)
            mock_session.get = MagicMock(return_value=mock_response)
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock(return_value=None)
            mock_session_cls.return_value = mock_session

            result = await source._fetch_issues(
                token="test_token",
                project_id="group/project",
                state="opened",
                labels=[],
            )
            self.assertEqual(result, [])

    async def test_poll_continues_after_api_error(self) -> None:
        """Poll should continue to other sources after one source fails."""
        # Create two projects
        self.db.insert_project(
            "proj-gl-2", "GitLab Project 2", "gitlab-proj-2", None,
            provider="gitlab",
            source_token_id="gl-tok-1",
            source_repo="group/project2",
            issue_state="opened",
            source_enabled=True,
            poll_interval_seconds=10,
        )

        source = GitLabIssuesSource()
        ctx = {"db": self.db}

        call_count = {"value": 0}
        async def mock_fetch(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] == 1:
                # First call fails
                raise aiohttp.ContentTypeError(
                    MagicMock(),
                    (),
                    message="text/html"
                )
            # Second call succeeds
            return []

        with patch.object(source, "_fetch_issues", side_effect=mock_fetch):
            # Should not crash, should try both projects
            try:
                await source.poll(ctx)
            except aiohttp.ContentTypeError:
                self.fail("Poll should handle errors from individual sources gracefully")


class ChannelRecordAttributeTests(unittest.TestCase):
    """Tests to verify ChannelRecord attributes are used correctly."""

    def test_channel_record_has_type_attribute(self) -> None:
        """ChannelRecord should have 'type' not 'channel_type'."""
        record = ChannelRecord(
            id="test-id",
            agent_id="agent-id",
            type="slack",
            name="test-channel",
            external_channel_id="C12345",
            enabled=True,
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        # Should have 'type' attribute
        self.assertEqual(record.type, "slack")

        # Should NOT have 'channel_type' attribute
        self.assertFalse(hasattr(record, "channel_type"))

    def test_channel_record_type_values(self) -> None:
        """ChannelRecord type can hold various channel types."""
        for channel_type in ["slack", "telegram", "discord", "web"]:
            record = ChannelRecord(
                id=str(uuid.uuid4()),
                agent_id="agent-id",
                type=channel_type,
                name=f"test-{channel_type}",
                external_channel_id="12345",
                enabled=True,
                created_at="2024-01-01T00:00:00Z",
                updated_at="2024-01-01T00:00:00Z",
            )
            self.assertEqual(record.type, channel_type)


class SessionsChannelDispatchTests(unittest.IsolatedAsyncioTestCase):
    """Tests for channel dispatch in sessions.py using correct attributes."""

    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_slack_channel_dispatch_uses_type_attribute(self) -> None:
        """Verify sessions code uses channel.type not channel.channel_type."""
        # Create agent with slack channel
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

        channel_id = str(uuid.uuid4())
        self.db.insert_channel(
            channel_id=channel_id,
            agent_id=agent_id,
            channel_type="slack",
            name="test-slack",
            external_channel_id="C12345",
            enabled=True,
        )

        # Verify channel is retrieved correctly
        channels = self.db.list_channels(agent_id=agent_id)
        self.assertEqual(len(channels), 1)
        channel = channels[0]

        # This is what the sessions.py code should use
        self.assertEqual(channel.type, "slack")

        # This would fail - verifying the bug is fixed
        with self.assertRaises(AttributeError):
            _ = channel.channel_type


if __name__ == "__main__":
    unittest.main()
