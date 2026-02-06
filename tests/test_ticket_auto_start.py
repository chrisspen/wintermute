import os
import tempfile
import unittest
import uuid

from asgiref.sync import async_to_sync

from wintermute.db import AsyncDatabase, Database
from wintermute.sources.tickets import TicketAutoStartSource


class TicketAutoStartSourceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_sync = Database(self.temp_db.name)
        self.db_sync.initialize()
        self.db = AsyncDatabase(self.temp_db.name)
        self.source = TicketAutoStartSource()

        async def setup():
            await self.db.upsert_task_source(
                self.source.id,
                True,
                self.source.base_priority,
                self.source.poll_interval_seconds,
                config={},
            )
            self.project_id = str(uuid.uuid4())
            await self.db.insert_project(
                project_id=self.project_id,
                name="Test Project",
                slug=f"tst-{str(uuid.uuid4())[:8]}",
                slack_channel_id=None,
            )

        async_to_sync(setup)()

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_poll_filters_auto_start_tickets(self) -> None:

        async def run_test():
            internal_ticket_id = str(uuid.uuid4())
            await self.db.insert_ticket(
                ticket_id=internal_ticket_id,
                project_id=self.project_id,
                agent_id=None,
                title="Internal task",
                description=None,
                assigned_to=None,
                estimate=None,
                status="open",
                source_url=None,
                auto_start=True,
            )
            await self.db.insert_ticket(
                ticket_id="github:source:123",
                project_id=self.project_id,
                agent_id=None,
                title="External task",
                description=None,
                assigned_to=None,
                estimate=None,
                status="open",
                source_url=None,
                auto_start=True,
            )
            await self.db.insert_ticket(
                ticket_id=str(uuid.uuid4()),
                project_id=self.project_id,
                agent_id=None,
                title="Done task",
                description=None,
                assigned_to=None,
                estimate=None,
                status="done",
                source_url=None,
                auto_start=True,
            )
            drafts = await self.source.poll({"db": self.db})
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0].checkpoint.get("ticket_id"), internal_ticket_id)

        async_to_sync(run_test)()


if __name__ == "__main__":
    unittest.main()
