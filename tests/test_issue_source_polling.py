"""Tests for per-project poll interval behavior (issue sources merged into projects)."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync, sync_to_async
from django.test import TestCase

from wintermute.db import AsyncDatabase, Database
from wintermute.models import Project, RemoteToken
from wintermute.sources.github import GitHubIssuesSource
from wintermute.sources.gitlab import GitLabIssuesSource


class GitHubIssuesSourcePollTests(TestCase):

    def setUp(self) -> None:
        # Django TestCase uses test database automatically
        # Use AsyncDatabase since sources expect async interface
        self.db = AsyncDatabase(":memory:") # Path ignored, uses Django's test DB
        # Create token required for projects with sources
        RemoteToken.objects.create(
            id="gh-tok-1",
            provider="github",
            user_login="testuser",
            token="ghp_faketoken",
        )

    def test_respects_per_source_poll_interval(self) -> None:
        """Each project with source should only poll after its poll_interval_seconds."""

        async def run_test():
            # Create project with GitHub source and 30 second interval
            await self.db.insert_project(
                "proj-1",
                "Test Project",
                "test-proj",
                None,
                provider="github",
                source_token_id="gh-tok-1",
                source_repo="testowner/testrepo",
                issue_state="open",
                source_enabled=True,
                poll_interval_seconds=30,
            )
            source = GitHubIssuesSource()
            ctx = {"db": self.db}

            with patch.object(source, "_fetch_issues", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = []

                # First poll should happen
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 1)

                # Immediate second poll should be skipped (within 30s interval)
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 1)

        async_to_sync(run_test)()

    def test_polls_after_interval_elapsed(self) -> None:
        """Source should poll again after its interval has elapsed."""

        async def run_test():
            await self.db.insert_project(
                "proj-2",
                "Test Project 2",
                "test-proj-2",
                None,
                provider="github",
                source_token_id="gh-tok-1",
                source_repo="owner/repo2",
                issue_state="open",
                source_enabled=True,
                poll_interval_seconds=60,
            )
            source = GitHubIssuesSource()
            ctx = {"db": self.db}

            with patch.object(source, "_fetch_issues", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = []

                # First poll
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 1)

                # Manually set last_poll to 61 seconds ago (project ID is now the source ID)
                source._last_poll["proj-2"] = datetime.now(timezone.utc).timestamp() - 61
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 2)

        async_to_sync(run_test)()

    def test_multiple_sources_independent_intervals(self) -> None:
        """Multiple projects with sources should track their intervals independently."""

        async def run_test():
            await self.db.insert_project(
                "proj-fast",
                "Fast Project",
                "fast-proj",
                None,
                provider="github",
                source_token_id="gh-tok-1",
                source_repo="owner/fast",
                issue_state="open",
                source_enabled=True,
                poll_interval_seconds=10,
            )
            await self.db.insert_project(
                "proj-slow",
                "Slow Project",
                "slow-proj",
                None,
                provider="github",
                source_token_id="gh-tok-1",
                source_repo="owner/slow",
                issue_state="open",
                source_enabled=True,
                poll_interval_seconds=300,
            )
            source = GitHubIssuesSource()
            ctx = {"db": self.db}

            with patch.object(source, "_fetch_issues", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = []

                # First poll - both should be called
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 2)

                # Set fast source to 11 seconds ago (past interval), slow source stays at 0
                source._last_poll["proj-fast"] = datetime.now(timezone.utc).timestamp() - 11
                await source.poll(ctx)
                # Only fast should be polled again
                self.assertEqual(mock_fetch.call_count, 3)

        async_to_sync(run_test)()

    def test_disabled_source_not_polled(self) -> None:
        """Disabled project sources should not be polled."""

        async def run_test():
            await self.db.insert_project(
                "proj-disabled",
                "Disabled Project",
                "disabled-proj",
                None,
                provider="github",
                source_token_id="gh-tok-1",
                source_repo="owner/disabled",
                issue_state="open",
                source_enabled=False,
                poll_interval_seconds=10,
            )
            source = GitHubIssuesSource()
            ctx = {"db": self.db}

            with patch.object(source, "_fetch_issues", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = []
                await source.poll(ctx)
                mock_fetch.assert_not_called()

        async_to_sync(run_test)()


class GitLabIssuesSourcePollTests(TestCase):

    def setUp(self) -> None:
        self.db = AsyncDatabase(":memory:")
        RemoteToken.objects.create(
            id="gl-tok-1",
            provider="gitlab",
            user_login="testuser",
            token="glpat_faketoken",
        )

    def test_respects_per_source_poll_interval(self) -> None:
        """GitLab project source should respect poll intervals."""

        async def run_test():
            await self.db.insert_project(
                "proj-gl-1",
                "GitLab Project",
                "gitlab-proj",
                None,
                provider="gitlab",
                source_token_id="gl-tok-1",
                source_repo="group/project",
                issue_state="opened",
                source_enabled=True,
                poll_interval_seconds=45,
            )
            source = GitLabIssuesSource()
            ctx = {"db": self.db}

            with patch.object(source, "_fetch_issues", new_callable=AsyncMock) as mock_fetch:
                mock_fetch.return_value = []

                # First poll should happen
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 1)

                # Immediate second poll should be skipped
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 1)

                # After interval elapsed
                source._last_poll["proj-gl-1"] = datetime.now(timezone.utc).timestamp() - 46
                await source.poll(ctx)
                self.assertEqual(mock_fetch.call_count, 2)

        async_to_sync(run_test)()


if __name__ == "__main__":
    import unittest
    unittest.main()
