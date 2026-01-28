"""Unit tests for ticket list improvements (WM-12 through WM-16)."""

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


class BaseTicketListTest(unittest.TestCase):
    """Base class with common setup for ticket list tests."""

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


class TicketAssigneeFilterTests(BaseTicketListTest):
    """WM-16: Tests for filtering tickets by assignee."""

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

        # Create tickets with different assignees
        self.ticket1_id = str(uuid.uuid4())
        self.ticket2_id = str(uuid.uuid4())
        self.ticket3_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket1_id,
            project_id=self.project_id,
            title="Agent Ticket",
            description=None,
            assigned_to=f"agent:{self.agent_id}",
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket2_id,
            project_id=self.project_id,
            title="User Ticket",
            description=None,
            assigned_to=f"user:{self.user_id}",
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket3_id,
            project_id=self.project_id,
            title="Unassigned Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

    def test_assignee_filter_dropdown_present(self) -> None:
        """Test that assignee filter dropdown appears on ticket list."""
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('data-table-filter="assigned_to"', html)
        self.assertIn("Assignee:", html)
        self.assertIn("All Assignees", html)

    def test_filter_by_agent_assignee(self) -> None:
        """Test filtering tickets by agent assignee."""
        response = self.client.get(f"/ui/tickets?assigned_to=agent:{self.agent_id}")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("Agent Ticket", html)
        self.assertNotIn("User Ticket", html)
        self.assertNotIn("Unassigned Ticket", html)

    def test_filter_by_user_assignee(self) -> None:
        """Test filtering tickets by user assignee."""
        response = self.client.get(f"/ui/tickets?assigned_to=user:{self.user_id}")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertNotIn("Agent Ticket", html)
        self.assertIn("User Ticket", html)
        self.assertNotIn("Unassigned Ticket", html)

    def test_database_filter_by_assigned_to(self) -> None:
        """Test database list_tickets filters by assigned_to."""
        tickets = self.db.list_tickets(assigned_to=f"agent:{self.agent_id}")
        self.assertEqual(len(tickets), 1)
        self.assertEqual(tickets[0].title, "Agent Ticket")


class TicketAssigneeDisplayTests(BaseTicketListTest):
    """WM-15: Tests for showing agent slug instead of UUID in assignee column."""

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

        # Create test agent with specific slug
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="My Test Agent",
            slug="my-test-agent",
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

        # Create ticket assigned to agent
        self.ticket_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket_id,
            project_id=self.project_id,
            title="Test Ticket",
            description=None,
            assigned_to=f"agent:{self.agent_id}",
            estimate=None,
            status="open",
        )

    def _show_assignee_column(self) -> None:
        """Set column preferences to include assigned_to column."""
        self.client.post(
            "/ui/column-preferences",
            data={
                "model": "tickets",
                "columns": '["name","title","assigned_to"]',
                "return_to": "/ui/tickets",
            },
            follow_redirects=False,
        )

    def test_assignee_shows_agent_slug(self) -> None:
        """Test that assignee column shows agent slug instead of UUID."""
        self._show_assignee_column()
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should show agent slug in the table row
        self.assertIn("agent:my-test-agent", html)
        # The cell should NOT display the raw UUID format "agent:UUID"
        # (Note: UUID still appears in dropdown options/filter values, which is fine)
        self.assertNotIn(f"agent:{self.agent_id}</span>", html)
        self.assertNotIn(f"agent:{self.agent_id}<", html)

    def test_assignee_shows_username_for_user(self) -> None:
        """Test that assignee column shows username for user assignments."""
        # Update ticket to be assigned to user
        self.db.update_ticket(self.ticket_id, assigned_to=f"user:{self.user_id}")
        self._show_assignee_column()
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should show username, not UUID
        self.assertIn("user:testuser", html)


class TicketDefaultAgentTests(BaseTicketListTest):
    """WM-14: Tests for auto-setting default agent when creating ticket."""

    def setUp(self) -> None:
        super().setUp()
        # Create test agent
        self.agent_id = str(uuid.uuid4())
        self.db.insert_agent(
            agent_id=self.agent_id,
            name="Default Agent",
            slug="default-agent",
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

        # Create test project with default agent set
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Project With Default",
            slug="project-with-default",
            slack_channel_id=None,
            source_agent_id=self.agent_id,
        )

        # Create project without default agent
        self.project_no_default_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_no_default_id,
            name="Project Without Default",
            slug="project-without-default",
            slack_channel_id=None,
        )

    def test_create_ticket_page_preselects_default_agent(self) -> None:
        """Test that create ticket page preselects the project's default agent."""
        response = self.client.get(f"/ui/tickets/create?project_id={self.project_id}")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should have the agent option selected
        self.assertIn(f'value="agent:{self.agent_id}" selected', html)

    def test_create_ticket_page_no_preselect_without_default(self) -> None:
        """Test that create ticket page doesn't preselect if no default agent."""
        response = self.client.get(f"/ui/tickets/create?project_id={self.project_no_default_id}")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Unassigned should be selected (no other selected attribute)
        # The agent option should not have 'selected'
        self.assertNotIn(f'value="agent:{self.agent_id}" selected', html)

    def test_create_ticket_page_has_js_for_project_change(self) -> None:
        """Test that create page includes JS for updating default on project change."""
        response = self.client.get("/ui/tickets/create")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("projectDefaultAgents", html)


class TicketPrioritySortTests(BaseTicketListTest):
    """WM-13: Tests for sorting tickets by priority column."""

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

        # Create tickets with different priorities
        self.ticket_high = str(uuid.uuid4())
        self.ticket_medium = str(uuid.uuid4())
        self.ticket_low = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket_high,
            project_id=self.project_id,
            title="High Priority Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
            priority="high",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket_medium,
            project_id=self.project_id,
            title="Medium Priority Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
            priority="medium",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket_low,
            project_id=self.project_id,
            title="Low Priority Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
            priority="low",
        )

    def _show_priority_column(self) -> None:
        """Set column preferences to include priority column."""
        self.client.post(
            "/ui/column-preferences",
            data={
                "model": "tickets",
                "columns": '["name","title","priority"]',
                "return_to": "/ui/tickets",
            },
            follow_redirects=False,
        )

    def test_priority_column_is_sortable(self) -> None:
        """Test that priority column has sortable attribute."""
        self._show_priority_column()
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn('data-sortable-column="priority"', html)

    def test_sort_by_priority_ascending(self) -> None:
        """Test sorting by priority ascending."""
        response = self.client.get("/ui/tickets?sort=priority")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Alphabetically: high < low < medium
        high_pos = html.find("High Priority Ticket")
        low_pos = html.find("Low Priority Ticket")
        medium_pos = html.find("Medium Priority Ticket")
        self.assertGreater(low_pos, high_pos)
        self.assertGreater(medium_pos, low_pos)

    def test_sort_by_priority_descending(self) -> None:
        """Test sorting by priority descending."""
        response = self.client.get("/ui/tickets?sort=-priority")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Reverse alphabetically: medium > low > high
        high_pos = html.find("High Priority Ticket")
        low_pos = html.find("Low Priority Ticket")
        medium_pos = html.find("Medium Priority Ticket")
        self.assertGreater(low_pos, medium_pos)
        self.assertGreater(high_pos, low_pos)


class TicketSearchFilterPreservationTests(BaseTicketListTest):
    """WM-12: Tests for search preserving filters."""

    def setUp(self) -> None:
        super().setUp()
        # Create test projects
        self.project1_id = str(uuid.uuid4())
        self.project2_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project1_id,
            name="Project Alpha",
            slug="project-alpha",
            slack_channel_id=None,
        )
        self.db.insert_project(
            self.project2_id,
            name="Project Beta",
            slug="project-beta",
            slack_channel_id=None,
        )

        # Create tickets
        self.ticket1_id = str(uuid.uuid4())
        self.ticket2_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket1_id,
            project_id=self.project1_id,
            title="Alpha Searchable",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket2_id,
            project_id=self.project2_id,
            title="Beta Searchable",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

    def test_search_form_includes_filter_hidden_fields(self) -> None:
        """Test that search form includes hidden fields for active filters."""
        response = self.client.get(f"/ui/tickets?project_id={self.project1_id}&status=open")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should have hidden inputs for filters
        self.assertIn(f'name="project_id" value="{self.project1_id}"', html)
        self.assertIn('name="status" value="open"', html)

    def test_search_form_no_hidden_for_empty_filter(self) -> None:
        """Test that search form doesn't include hidden fields for empty filters."""
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should not have hidden input for project_id since it's empty
        self.assertNotIn('type="hidden" name="project_id"', html)

    def test_safe_return_to_strips_saved_param(self) -> None:
        """Test that saved param is stripped from return_to to prevent accumulation."""
        # When we visit the page with saved param, column picker return_to should not include it
        response = self.client.get("/ui/tickets?saved=1&project_id=abc")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # The return_to in the column picker form should not have saved=1
        # But should still have project_id
        self.assertIn('name="return_to" value="/ui/tickets?project_id=abc"', html)
        self.assertNotIn('saved=1', html.split('name="return_to"')[1].split('/>')[0])


if __name__ == "__main__":
    unittest.main()
