"""Unit tests for Ticket API endpoints."""

import os
import tempfile
import unittest
import uuid

from fastapi.testclient import TestClient

from wintermute.db import Database
from wintermute.web.app import create_app


class TicketPatchAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        # Set a known secret for session signing
        os.environ["WINTERMUTE_WEB_SECRET"] = "test-secret-key"
        app = create_app(self.db)
        self.client = TestClient(app)
        # Create a test user
        self.db.insert_user(
            user_id=str(uuid.uuid4()),
            username="testuser",
            password_hash="testhash",
            salt="testsalt",
        )
        # Create a test project
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )
        # Create a test ticket
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
        # Login to get session cookie
        with self.client:
            # Manually set session via the login flow workaround
            # Since we can't easily fake the password, we'll use the API token approach
            pass
        # Use API token for auth instead of session
        self.api_token = str(uuid.uuid4())
        self.db.insert_api_token(
            self.api_token,
            name="Test Token",
            token=self.api_token,
            permissions={"tickets": {"read": True, "update": True}},
        )

    def tearDown(self) -> None:
        self.temp_db.close()

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_token}"}

    def test_patch_ticket_status(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"status": "in-progress"},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["ticket"]["status"], "in-progress")
        # Verify in database
        ticket = self.db.get_ticket(self.ticket_id)
        self.assertEqual(ticket.status, "in-progress")

    def test_patch_ticket_priority(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"priority": "high"},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["ticket"]["priority"], "high")
        # Verify in database
        ticket = self.db.get_ticket(self.ticket_id)
        self.assertEqual(ticket.priority, "high")

    def test_patch_ticket_priority_none(self) -> None:
        # First set a priority
        self.db.update_ticket(self.ticket_id, priority="medium")
        # Then clear it
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"priority": None},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIsNone(data["ticket"]["priority"])
        # Verify in database
        ticket = self.db.get_ticket(self.ticket_id)
        self.assertIsNone(ticket.priority)

    def test_patch_ticket_story_points(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"story_points": 5.5},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["ticket"]["story_points"], 5.5)
        # Verify in database
        ticket = self.db.get_ticket(self.ticket_id)
        self.assertEqual(ticket.story_points, 5.5)

    def test_patch_ticket_story_points_clear(self) -> None:
        # First set story points
        self.db.update_ticket(self.ticket_id, story_points=3.0)
        # Then clear them
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"story_points": ""},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIsNone(data["ticket"]["story_points"])

    def test_patch_ticket_multiple_fields(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"status": "done", "priority": "low", "story_points": 2},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["ticket"]["status"], "done")
        self.assertEqual(data["ticket"]["priority"], "low")
        self.assertEqual(data["ticket"]["story_points"], 2.0)

    def test_patch_ticket_invalid_status(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"status": "invalid-status"},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid status", data["detail"])

    def test_patch_ticket_invalid_priority(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"priority": "urgent"},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid priority", data["detail"])

    def test_patch_ticket_invalid_story_points(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={"story_points": "not-a-number"},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("Invalid story_points", data["detail"])

    def test_patch_ticket_not_found(self) -> None:
        response = self.client.patch(
            "/api/tickets/nonexistent-id",
            json={"status": "open"},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 404)

    def test_patch_ticket_no_fields(self) -> None:
        response = self.client.patch(
            f"/api/tickets/{self.ticket_id}",
            json={},
            headers=self._auth_headers(),
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertIn("No valid fields", data["detail"])


if __name__ == "__main__":
    unittest.main()
