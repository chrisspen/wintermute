"""Tests for per-IssueSource poll interval behavior."""

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
        # Create project and token required for issue sources
        self.db.insert_project("proj-1", "Test Project", "test-proj", None)
        self.db.insert_remote_token(
            token_id="gh-tok-1",
            provider="github",
            user_login="testuser",
            token="ghp_faketoken",
        )

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_respects_per_source_poll_interval(self) -> None:
        """Each IssueSource should only poll after its own poll_interval_seconds."""
        # Create issue source with 30 second interval
        self.db.insert_issue_source(
            "src-1",
            provider="github",
            token_id="gh-tok-1",
            agent_id=None,
            project_id="proj-1",
            repo="testowner/testrepo",
            state="open",
            labels=[],
            enabled=True,
            auto_start=False,
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
        self.db.insert_issue_source(
            "src-2",
            provider="github",
            token_id="gh-tok-1",
            agent_id=None,
            project_id="proj-1",
            repo="owner/repo2",
            state="open",
            labels=[],
            enabled=True,
            auto_start=False,
            poll_interval_seconds=60,
        )
        source = GitHubIssuesSource()
        ctx = {"db": self.db}

        with patch.object(source, "_fetch_issues", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []

            # First poll
            await source.poll(ctx)
            self.assertEqual(mock_fetch.call_count, 1)

            # Manually set last_poll to 61 seconds ago
            source._last_poll["src-2"] = datetime.now(timezone.utc).timestamp() - 61
            await source.poll(ctx)
            self.assertEqual(mock_fetch.call_count, 2)

    async def test_multiple_sources_independent_intervals(self) -> None:
        """Multiple issue sources should track their intervals independently."""
        self.db.insert_issue_source(
            "src-fast",
            provider="github",
            token_id="gh-tok-1",
            agent_id=None,
            project_id="proj-1",
            repo="owner/fast",
            state="open",
            labels=[],
            enabled=True,
            auto_start=False,
            poll_interval_seconds=10,
        )
        self.db.insert_issue_source(
            "src-slow",
            provider="github",
            token_id="gh-tok-1",
            agent_id=None,
            project_id="proj-1",
            repo="owner/slow",
            state="open",
            labels=[],
            enabled=True,
            auto_start=False,
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
            source._last_poll["src-fast"] = datetime.now(timezone.utc).timestamp() - 11
            await source.poll(ctx)
            # Only fast should be polled again
            self.assertEqual(mock_fetch.call_count, 3)

    async def test_disabled_source_not_polled(self) -> None:
        """Disabled issue sources should not be polled."""
        self.db.insert_issue_source(
            "src-disabled",
            provider="github",
            token_id="gh-tok-1",
            agent_id=None,
            project_id="proj-1",
            repo="owner/disabled",
            state="open",
            labels=[],
            enabled=False,
            auto_start=False,
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
        self.db.insert_project("proj-1", "Test Project", "test-proj", None)
        self.db.insert_remote_token(
            token_id="gl-tok-1",
            provider="gitlab",
            user_login="testuser",
            token="glpat_faketoken",
        )

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_respects_per_source_poll_interval(self) -> None:
        """GitLab source should respect per-source poll intervals."""
        self.db.insert_issue_source(
            "gl-src-1",
            provider="gitlab",
            token_id="gl-tok-1",
            agent_id=None,
            project_id="proj-1",
            repo="group/project",
            state="opened",
            labels=[],
            enabled=True,
            auto_start=False,
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
            source._last_poll["gl-src-1"] = datetime.now(timezone.utc).timestamp() - 46
            await source.poll(ctx)
            self.assertEqual(mock_fetch.call_count, 2)


if __name__ == "__main__":
    unittest.main()
