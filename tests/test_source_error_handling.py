"""Tests for error handling in sources and related code."""

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import aiohttp
from asgiref.sync import async_to_sync

from wintermute.db import AsyncDatabase, ChannelRecord, Database
from wintermute.models import RemoteToken
from wintermute.sources.gitlab import GitLabIssuesSource


class GitLabAPIErrorHandlingTests(unittest.TestCase):
    """Tests for GitLab source handling API errors gracefully."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_sync = Database(self.temp_db.name)
        self.db_sync.initialize()
        self.db = AsyncDatabase(self.temp_db.name)

        # Create token required for projects with sources
        self.token_id = f"gl-tok-{uuid.uuid4()}"
        self.db_sync.insert_remote_token(
            token_id=self.token_id,
            provider="gitlab",
            user_login="testuser",
            token="glpat_faketoken",
        )

        self.project_id = f"proj-gl-{uuid.uuid4()}"
        self.unique_slug = str(uuid.uuid4()).replace("-", "")[:12]

        async def create_project():
            await self.db.insert_project(
                self.project_id,
                "GitLab Project",
                self.unique_slug,
                None,
                provider="gitlab",
                source_token_id=self.token_id,
                source_repo="group/project",
                issue_state="opened",
                source_enabled=True,
                poll_interval_seconds=10,
            )

        async_to_sync(create_project)()

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_handles_500_html_response(self) -> None:
        """GitLab source should not crash when API returns 500 with HTML error page."""

        async def run_test():
            source = GitLabIssuesSource()
            ctx = {"db": self.db}

            # Create a mock response that returns HTML (like GitLab error pages)
            mock_response = AsyncMock()
            mock_response.status = 500
            mock_response.json = AsyncMock(
                side_effect=aiohttp.ContentTypeError(MagicMock(), (), message="Attempt to decode JSON with unexpected mimetype: text/html")
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

        async_to_sync(run_test)()

    def test_handles_400_error(self) -> None:
        """GitLab source should return empty list on 400 errors."""

        async def run_test():
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

        async_to_sync(run_test)()

    def test_poll_continues_after_api_error(self) -> None:
        """Poll should continue to other sources after one source fails."""

        async def run_test():
            # Create second project with unique IDs
            project2_id = f"proj-gl-{uuid.uuid4()}"
            unique_slug2 = str(uuid.uuid4()).replace("-", "")[:12]
            await self.db.insert_project(
                project2_id,
                "GitLab Project 2",
                unique_slug2,
                None,
                provider="gitlab",
                source_token_id=self.token_id,
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
                    raise aiohttp.ContentTypeError(MagicMock(), (), message="text/html")
                # Second call succeeds
                return []

            with patch.object(source, "_fetch_issues", side_effect=mock_fetch):
                # Should not crash, should try both projects
                try:
                    await source.poll(ctx)
                except aiohttp.ContentTypeError:
                    self.fail("Poll should handle errors from individual sources gracefully")

        async_to_sync(run_test)()


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


class SessionsChannelDispatchTests(unittest.TestCase):
    """Tests for channel dispatch in sessions.py using correct attributes."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_sync = Database(self.temp_db.name)
        self.db_sync.initialize()
        self.db = AsyncDatabase(self.temp_db.name)

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_slack_channel_dispatch_uses_type_attribute(self) -> None:
        """Verify sessions code uses channel.type not channel.channel_type."""

        async def run_test():
            # Create agent with unique IDs
            agent_id = str(uuid.uuid4())
            agent_slug = f"test-agent-{uuid.uuid4()}"
            await self.db.insert_agent(
                agent_id=agent_id,
                name="Test Agent",
                slug=agent_slug,
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
            await self.db.insert_channel(
                channel_id=channel_id,
                agent_id=agent_id,
                channel_type="slack",
                name="test-slack",
                external_channel_id="C12345",
                enabled=True,
            )

            # Verify channel is retrieved correctly
            channels = await self.db.list_channels(agent_id=agent_id)
            self.assertEqual(len(channels), 1)
            channel = channels[0]

            # This is what the sessions.py code should use
            self.assertEqual(channel.type, "slack")

            # This would fail - verifying the bug is fixed
            with self.assertRaises(AttributeError):
                _ = channel.channel_type

        async_to_sync(run_test)()


if __name__ == "__main__":
    unittest.main()
