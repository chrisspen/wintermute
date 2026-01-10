import tempfile
import unittest
import uuid

from wintermute.db import Database
from wintermute.sources.tickets import TicketAutoStartSource


class TicketAutoStartSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()
        self.source = TicketAutoStartSource()
        self.db.upsert_task_source(
            self.source.id,
            True,
            self.source.base_priority,
            self.source.poll_interval_seconds,
            config={},
        )
        self.project_id = str(uuid.uuid4())
        self.db.insert_project(
            project_id=self.project_id,
            name="Test Project",
            slug="test-project",
            slack_channel_id=None,
        )

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_poll_filters_auto_start_tickets(self) -> None:
        internal_ticket_id = str(uuid.uuid4())
        self.db.insert_ticket(
            ticket_id=internal_ticket_id,
            project_id=self.project_id,
            agent_id=None,
            title="Internal task",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
            internal_notes=None,
            source_url=None,
            auto_start=True,
        )
        self.db.insert_ticket(
            ticket_id="github:source:123",
            project_id=self.project_id,
            agent_id=None,
            title="External task",
            description=None,
            assigned_to=None,
            estimate=None,
            status="open",
            internal_notes=None,
            source_url=None,
            auto_start=True,
        )
        self.db.insert_ticket(
            ticket_id=str(uuid.uuid4()),
            project_id=self.project_id,
            agent_id=None,
            title="Done task",
            description=None,
            assigned_to=None,
            estimate=None,
            status="done",
            internal_notes=None,
            source_url=None,
            auto_start=True,
        )
        drafts = await self.source.poll({"db": self.db})
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].checkpoint.get("ticket_id"), internal_ticket_id)


if __name__ == "__main__":
    unittest.main()
