"""Unit tests for ticket count uniqueness within projects."""

import os
import tempfile
import threading
import unittest
import uuid

from wintermute.db import Database


class TicketCountTests(unittest.TestCase):
    """Tests for ticket count field uniqueness per project."""

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
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

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_tickets_get_sequential_counts_in_same_project(self) -> None:
        """Test that tickets in the same project get sequential counts."""
        ticket1_id = str(uuid.uuid4())
        ticket2_id = str(uuid.uuid4())
        ticket3_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=ticket1_id,
            project_id=self.project1_id,
            title="First Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=ticket2_id,
            project_id=self.project1_id,
            title="Second Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=ticket3_id,
            project_id=self.project1_id,
            title="Third Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        t1 = self.db.get_ticket(ticket1_id)
        t2 = self.db.get_ticket(ticket2_id)
        t3 = self.db.get_ticket(ticket3_id)
        self.assertEqual(t1.count, 1)
        self.assertEqual(t2.count, 2)
        self.assertEqual(t3.count, 3)

    def test_tickets_in_different_projects_have_independent_counts(self) -> None:
        """Test that tickets in different projects have independent count sequences."""
        ticket_p1_1 = str(uuid.uuid4())
        ticket_p1_2 = str(uuid.uuid4())
        ticket_p2_1 = str(uuid.uuid4())
        ticket_p2_2 = str(uuid.uuid4())
        # Insert into project 1
        self.db.insert_ticket(
            ticket_id=ticket_p1_1,
            project_id=self.project1_id,
            title="P1 First Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=ticket_p1_2,
            project_id=self.project1_id,
            title="P1 Second Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        # Insert into project 2
        self.db.insert_ticket(
            ticket_id=ticket_p2_1,
            project_id=self.project2_id,
            title="P2 First Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        self.db.insert_ticket(
            ticket_id=ticket_p2_2,
            project_id=self.project2_id,
            title="P2 Second Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        # Verify counts are independent per project
        t_p1_1 = self.db.get_ticket(ticket_p1_1)
        t_p1_2 = self.db.get_ticket(ticket_p1_2)
        t_p2_1 = self.db.get_ticket(ticket_p2_1)
        t_p2_2 = self.db.get_ticket(ticket_p2_2)
        self.assertEqual(t_p1_1.count, 1)
        self.assertEqual(t_p1_2.count, 2)
        self.assertEqual(t_p2_1.count, 1)
        self.assertEqual(t_p2_2.count, 2)

    def test_ticket_name_uses_project_symbol_and_count(self) -> None:
        """Test that ticket name property uses project symbol and count."""
        # Update project to have a symbol
        self.db.update_project(self.project1_id, symbol="ALPHA")
        ticket_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=ticket_id,
            project_id=self.project1_id,
            title="Test Ticket",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
        )
        ticket = self.db.get_ticket(ticket_id)
        self.assertEqual(ticket.name, "ALPHA-1")

    def test_concurrent_ticket_inserts_get_unique_counts(self) -> None:
        """Test that concurrent ticket inserts still get unique counts."""
        num_tickets = 10
        results = []
        errors = []

        def insert_ticket(i: int) -> None:
            try:
                ticket_id = str(uuid.uuid4())
                self.db.insert_ticket(
                    ticket_id=ticket_id,
                    project_id=self.project1_id,
                    title=f"Concurrent Ticket {i}",
                    description=None,
                    assigned_to=None,
                    estimate=None,
                    status="open",
                )
                results.append(ticket_id)
            except Exception as e:
                errors.append(str(e))

        # Launch concurrent inserts
        threads = []
        for i in range(num_tickets):
            t = threading.Thread(target=insert_ticket, args=(i,))
            threads.append(t)
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All inserts should succeed
        self.assertEqual(len(errors), 0, f"Errors occurred: {errors}")
        self.assertEqual(len(results), num_tickets)

        # All tickets should have unique counts
        counts = []
        for ticket_id in results:
            ticket = self.db.get_ticket(ticket_id)
            counts.append(ticket.count)
        self.assertEqual(len(set(counts)), num_tickets, f"Duplicate counts found: {sorted(counts)}")
        self.assertEqual(sorted(counts), list(range(1, num_tickets + 1)))


if __name__ == "__main__":
    unittest.main()
