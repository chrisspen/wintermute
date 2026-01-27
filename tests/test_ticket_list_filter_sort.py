"""Unit tests for ticket list filtering and sorting."""

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


class TicketListFilterTests(unittest.TestCase):
    """Tests for filtering tickets by project."""

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

        # Create tickets in each project
        self.ticket1_id = str(uuid.uuid4())
        self.ticket2_id = str(uuid.uuid4())
        self.ticket3_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket1_id,
            project_id=self.project1_id,
            title="Alpha Ticket 1",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket2_id,
            project_id=self.project1_id,
            title="Alpha Ticket 2",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=self.ticket3_id,
            project_id=self.project2_id,
            title="Beta Ticket 1",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_tickets_list_shows_all_without_filter(self) -> None:
        """Test that unfiltered list shows all tickets."""
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # All tickets should be visible
        self.assertIn("Alpha Ticket 1", html)
        self.assertIn("Alpha Ticket 2", html)
        self.assertIn("Beta Ticket 1", html)

    def test_tickets_list_filter_by_project(self) -> None:
        """Test filtering tickets by project_id."""
        # Filter by project 1
        response = self.client.get(f"/ui/tickets?project_id={self.project1_id}")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Only project 1 tickets should be visible
        self.assertIn("Alpha Ticket 1", html)
        self.assertIn("Alpha Ticket 2", html)
        self.assertNotIn("Beta Ticket 1", html)

    def test_tickets_list_filter_by_different_project(self) -> None:
        """Test filtering by a different project."""
        # Filter by project 2
        response = self.client.get(f"/ui/tickets?project_id={self.project2_id}")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Only project 2 tickets should be visible
        self.assertNotIn("Alpha Ticket 1", html)
        self.assertNotIn("Alpha Ticket 2", html)
        self.assertIn("Beta Ticket 1", html)

    def test_tickets_list_shows_filter_dropdown(self) -> None:
        """Test that the filter dropdown is present."""
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Filter dropdown should be present
        self.assertIn("data-table-filter", html)
        self.assertIn("Project:", html)
        self.assertIn("All Projects", html)


class TicketListSortTests(unittest.TestCase):
    """Tests for sorting tickets."""

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

        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create tickets with different update times
        self.ticket1_id = str(uuid.uuid4())
        self.ticket2_id = str(uuid.uuid4())
        self.ticket3_id = str(uuid.uuid4())

        # Create tickets with small delays to ensure different timestamps
        self.db.insert_ticket(
            ticket_id=self.ticket1_id,
            project_id=self.project_id,
            title="First Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        time.sleep(0.05)
        self.db.insert_ticket(
            ticket_id=self.ticket2_id,
            project_id=self.project_id,
            title="Second Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        time.sleep(0.05)
        self.db.insert_ticket(
            ticket_id=self.ticket3_id,
            project_id=self.project_id,
            title="Third Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def _show_updated_at_column(self) -> None:
        """Set column preferences to include updated_at column."""
        self.client.post(
            "/ui/column-preferences",
            data={
                "model": "tickets",
                "columns": '["name","title","updated_at"]',
                "return_to": "/ui/tickets",
            },
            follow_redirects=False,
        )

    def test_tickets_list_shows_sortable_column_header(self) -> None:
        """Test that updated_at column shows sort indicator when column is visible."""
        self._show_updated_at_column()
        response = self.client.get("/ui/tickets")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should have sortable column attribute for updated_at
        self.assertIn('data-sortable-column="updated_at"', html)

    def test_tickets_list_sort_by_updated_at_asc(self) -> None:
        """Test sorting by updated_at ascending."""
        response = self.client.get("/ui/tickets?sort=updated_at")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Check order - First should appear before Third
        first_pos = html.find("First Ticket")
        third_pos = html.find("Third Ticket")
        self.assertGreater(third_pos, first_pos)

    def test_tickets_list_sort_by_updated_at_desc(self) -> None:
        """Test sorting by updated_at descending."""
        response = self.client.get("/ui/tickets?sort=-updated_at")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Check order - Third should appear before First
        first_pos = html.find("First Ticket")
        third_pos = html.find("Third Ticket")
        self.assertGreater(first_pos, third_pos)

    def test_tickets_list_sort_shows_arrow_icon(self) -> None:
        """Test that sorting shows the direction arrow."""
        self._show_updated_at_column()
        response = self.client.get("/ui/tickets?sort=updated_at")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should show up arrow for ascending
        self.assertIn("fa-arrow-up", html)

    def test_tickets_list_sort_desc_shows_down_arrow(self) -> None:
        """Test that descending sort shows down arrow."""
        self._show_updated_at_column()
        response = self.client.get("/ui/tickets?sort=-updated_at")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should show down arrow for descending
        self.assertIn("fa-arrow-down", html)

    def test_tickets_list_sort_shows_remove_button(self) -> None:
        """Test that active sort shows remove button."""
        self._show_updated_at_column()
        response = self.client.get("/ui/tickets?sort=updated_at")
        self.assertEqual(response.status_code, 200)
        html = response.text
        # Should show remove sort button
        self.assertIn('data-remove-sort="updated_at"', html)

    def test_tickets_list_ignores_invalid_sort_column(self) -> None:
        """Test that invalid sort columns are ignored."""
        response = self.client.get("/ui/tickets?sort=invalid_column")
        self.assertEqual(response.status_code, 200)
        # Should not error and should show tickets


class TicketDatabaseSortTests(unittest.TestCase):
    """Tests for database-level sorting."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

        # Create test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

        # Create tickets
        self.ticket1_id = str(uuid.uuid4())
        self.ticket2_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=self.ticket1_id,
            project_id=self.project_id,
            title="First",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        time.sleep(0.05)
        self.db.insert_ticket(
            ticket_id=self.ticket2_id,
            project_id=self.project_id,
            title="Second",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_list_tickets_default_order(self) -> None:
        """Test default order is by created_at descending."""
        tickets = self.db.list_tickets()
        # Second ticket created later, should be first
        self.assertEqual(tickets[0].title, "Second")
        self.assertEqual(tickets[1].title, "First")

    def test_list_tickets_order_by_updated_at_asc(self) -> None:
        """Test ordering by updated_at ascending."""
        tickets = self.db.list_tickets(order_by=[("updated_at", "asc")])
        # First ticket updated first, should be first
        self.assertEqual(tickets[0].title, "First")
        self.assertEqual(tickets[1].title, "Second")

    def test_list_tickets_order_by_updated_at_desc(self) -> None:
        """Test ordering by updated_at descending."""
        tickets = self.db.list_tickets(order_by=[("updated_at", "desc")])
        # Second ticket updated later, should be first
        self.assertEqual(tickets[0].title, "Second")
        self.assertEqual(tickets[1].title, "First")

    def test_list_tickets_filter_and_sort_combined(self) -> None:
        """Test that filtering and sorting work together."""
        tickets = self.db.list_tickets(project_id=self.project_id, order_by=[("updated_at", "asc")])
        self.assertEqual(len(tickets), 2)
        self.assertEqual(tickets[0].title, "First")


if __name__ == "__main__":
    unittest.main()
