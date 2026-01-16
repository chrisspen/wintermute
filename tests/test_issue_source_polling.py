"""Tests for per-project poll interval behavior (issue sources merged into projects)."""

import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from wintermute.db import Database
from wintermute.sources.github import GitHubIssuesSource
from wintermute.sources.gitlab import GitLabIssuesSource


class GitHubIssuesSourcePollTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Create token required for projects with sources
        self.db.insert_remote_token(
            token_id="gh-tok-1",
            provider="github",
            user_login="testuser",
            token="ghp_faketoken",
        )

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_respects_per_source_poll_interval(self) -> None:
        """Each project with source should only poll after its poll_interval_seconds."""
        # Create project with GitHub source and 30 second interval
        self.db.insert_project(
            "proj-1", "Test Project", "test-proj", None,
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

    async def test_polls_after_interval_elapsed(self) -> None:
        """Source should poll again after its interval has elapsed."""
        self.db.insert_project(
            "proj-2", "Test Project 2", "test-proj-2", None,
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

    async def test_multiple_sources_independent_intervals(self) -> None:
        """Multiple projects with sources should track their intervals independently."""
        self.db.insert_project(
            "proj-fast", "Fast Project", "fast-proj", None,
            provider="github",
            source_token_id="gh-tok-1",
            source_repo="owner/fast",
            issue_state="open",
            source_enabled=True,
            poll_interval_seconds=10,
        )
        self.db.insert_project(
            "proj-slow", "Slow Project", "slow-proj", None,
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

    async def test_disabled_source_not_polled(self) -> None:
        """Disabled project sources should not be polled."""
        self.db.insert_project(
            "proj-disabled", "Disabled Project", "disabled-proj", None,
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


class GitLabIssuesSourcePollTests(unittest.IsolatedAsyncioTestCase):
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

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_respects_per_source_poll_interval(self) -> None:
        """GitLab project source should respect poll intervals."""
        self.db.insert_project(
            "proj-gl-1", "GitLab Project", "gitlab-proj", None,
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


if __name__ == "__main__":
    unittest.main()
