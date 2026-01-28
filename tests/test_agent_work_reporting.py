"""Unit tests for agent work reporting feature (WM-2)."""

import base64
import hashlib
import os
import tempfile
import time
import unittest
import uuid

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app


class BaseWorkReportingTest(unittest.TestCase):
    """Base class with common setup for work reporting tests."""

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

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)


class SecondsSpentWorkingTests(BaseWorkReportingTest):
    """Tests for seconds_spent_working field calculation."""

    def setUp(self) -> None:
        super().setUp()
        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create test agent
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

        # Create test ticket
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

    def test_agent_comment_calculates_seconds_since_previous(self) -> None:
        """Test that agent comments calculate seconds_spent_working from prev comment."""
        # Insert first comment (user comment)
        comment1_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=comment1_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=None, # User comment
            author=self.user_id,
            source_id=None,
            issue_number=None,
            body="User comment",
            public=False,
        )

        # Delay to ensure different timestamps (1+ seconds so int diff is > 0)
        time.sleep(1.1)

        # Insert agent comment
        comment2_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=comment2_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=self.agent_id, # Agent comment
            author=None,
            source_id=None,
            issue_number=None,
            body="Agent response",
            public=False,
        )

        # Get the agent comment and verify seconds_spent_working
        comment = self.db.get_comment(comment2_id)
        self.assertIsNotNone(comment)
        self.assertIsNotNone(comment.seconds_spent_working)
        # With 1.1 second delay, seconds_spent_working should be at least 1
        self.assertGreaterEqual(comment.seconds_spent_working, 1)

    def test_user_comment_has_no_seconds_spent_working(self) -> None:
        """Test that user comments don't have seconds_spent_working set."""
        comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=None, # User comment
            author=self.user_id,
            source_id=None,
            issue_number=None,
            body="User comment",
            public=False,
        )

        comment = self.db.get_comment(comment_id)
        self.assertIsNotNone(comment)
        self.assertIsNone(comment.seconds_spent_working)

    def test_first_agent_comment_has_no_seconds_spent_working(self) -> None:
        """Test that the first comment on a ticket has no previous to compare."""
        comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=self.agent_id,
            author=None,
            source_id=None,
            issue_number=None,
            body="First agent comment",
            public=False,
        )

        comment = self.db.get_comment(comment_id)
        self.assertIsNotNone(comment)
        # First comment has no previous, so seconds_spent_working should be None
        self.assertIsNone(comment.seconds_spent_working)


class AgentWorkReportTests(BaseWorkReportingTest):
    """Tests for agent work report queries."""

    def setUp(self) -> None:
        super().setUp()
        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create test sprint
        self.sprint_id = str(uuid.uuid4())
        self.db.insert_sprint(
            sprint_id=self.sprint_id,
            name="Sprint 1",
            start_date="2026-01-01",
            end_date="2026-01-14",
            status="active",
        )

        # Create test agent
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

        # Create test ticket in sprint
        self.ticket_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket_id,
            project_id=self.project_id,
            title="Test Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
            sprint_id=self.sprint_id,
        )

    def _create_agent_comment_with_work(self, seconds: int) -> str:
        """Create a user comment, wait, then create agent comment."""
        # First comment
        user_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=user_comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=None,
            author=self.user_id,
            source_id=None,
            issue_number=None,
            body="User prompt",
            public=False,
        )

        # Delay to ensure at least 1 second difference (for integer seconds)
        time.sleep(1.1)

        # Agent response
        agent_comment_id = str(uuid.uuid4())
        self.db.insert_comment(
            comment_id=agent_comment_id,
            ticket_id=self.ticket_id,
            session_id=None,
            project_id=self.project_id,
            agent_id=self.agent_id,
            author=None,
            source_id=None,
            issue_number=None,
            body="Agent response",
            public=False,
        )

        return agent_comment_id

    def test_get_agent_work_totals(self) -> None:
        """Test getting total work per agent."""
        self._create_agent_comment_with_work(60)

        totals = self.db.get_agent_work_totals()
        self.assertEqual(len(totals), 1)
        self.assertEqual(totals[0].agent_id, self.agent_id)
        self.assertEqual(totals[0].agent_name, "Test Agent")
        self.assertEqual(totals[0].agent_slug, "test-agent")
        self.assertGreater(totals[0].total_seconds, 0)

    def test_get_agent_work_by_year(self) -> None:
        """Test getting work by year."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_year()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertIsInstance(records[0].year, int)
        self.assertGreater(records[0].total_seconds, 0)

    def test_get_agent_work_by_year_month(self) -> None:
        """Test getting work by year/month."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_year_month()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertIsInstance(records[0].year, int)
        self.assertIsInstance(records[0].month, int)
        self.assertGreater(records[0].total_seconds, 0)

    def test_get_agent_work_by_sprint(self) -> None:
        """Test getting work by sprint."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_sprint()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertEqual(records[0].sprint_id, self.sprint_id)
        self.assertEqual(records[0].sprint_name, "Sprint 1")
        self.assertGreater(records[0].total_seconds, 0)

    def test_get_agent_work_by_project(self) -> None:
        """Test getting work by project."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_project()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertEqual(records[0].project_id, self.project_id)
        self.assertEqual(records[0].project_name, "Test Project")
        self.assertGreater(records[0].total_seconds, 0)

    def test_get_agent_work_by_project_year(self) -> None:
        """Test getting work by project/year."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_project_year()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertEqual(records[0].project_id, self.project_id)
        self.assertIsInstance(records[0].year, int)
        self.assertGreater(records[0].total_seconds, 0)

    def test_get_agent_work_by_project_year_month(self) -> None:
        """Test getting work by project/year/month."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_project_year_month()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertEqual(records[0].project_id, self.project_id)
        self.assertIsInstance(records[0].year, int)
        self.assertIsInstance(records[0].month, int)
        self.assertGreater(records[0].total_seconds, 0)

    def test_get_agent_work_by_project_sprint(self) -> None:
        """Test getting work by project/sprint."""
        self._create_agent_comment_with_work(60)

        records = self.db.get_agent_work_by_project_sprint()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].agent_id, self.agent_id)
        self.assertEqual(records[0].project_id, self.project_id)
        self.assertEqual(records[0].sprint_id, self.sprint_id)
        self.assertEqual(records[0].sprint_name, "Sprint 1")
        self.assertGreater(records[0].total_seconds, 0)


class ReportUITests(BaseWorkReportingTest):
    """Tests for report UI pages."""

    def test_report_nav_links_present(self) -> None:
        """Test that report navigation links appear in the sidebar."""
        response = self.client.get("/ui")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Reports", html)
        self.assertIn("/ui/reports/agent-work-totals", html)
        self.assertIn("/ui/reports/agent-work-by-year", html)
        self.assertIn("/ui/reports/agent-work-by-month", html)
        self.assertIn("/ui/reports/agent-work-by-sprint", html)
        self.assertIn("/ui/reports/agent-work-by-project", html)

    def test_agent_work_totals_page_loads(self) -> None:
        """Test that agent work totals report page loads."""
        response = self.client.get("/ui/reports/agent-work-totals")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work Totals", html)
        self.assertIn("Total time worked by each agent", html)

    def test_agent_work_by_year_page_loads(self) -> None:
        """Test that agent work by year report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-year")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Year", html)

    def test_agent_work_by_month_page_loads(self) -> None:
        """Test that agent work by month report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-month")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Month", html)

    def test_agent_work_by_sprint_page_loads(self) -> None:
        """Test that agent work by sprint report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-sprint")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Sprint", html)

    def test_agent_work_by_project_page_loads(self) -> None:
        """Test that agent work by project report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-project")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Project", html)

    def test_agent_work_by_project_year_page_loads(self) -> None:
        """Test that agent work by project/year report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-project-year")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Project/Year", html)

    def test_agent_work_by_project_month_page_loads(self) -> None:
        """Test that agent work by project/month report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-project-month")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Project/Month", html)

    def test_agent_work_by_project_sprint_page_loads(self) -> None:
        """Test that agent work by project/sprint report page loads."""
        response = self.client.get("/ui/reports/agent-work-by-project-sprint")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Work by Project/Sprint", html)


if __name__ == "__main__":
    unittest.main()
