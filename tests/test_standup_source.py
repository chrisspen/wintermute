import os
import tempfile
import unittest
from datetime import datetime, timezone

from asgiref.sync import async_to_sync

from wintermute.db import AsyncDatabase, Database
from wintermute.sources.standup import StandupSource


class StandupSourceTests(unittest.TestCase):

    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db_sync = Database(self.temp_db.name)
        self.db_sync.initialize()
        self.db = AsyncDatabase(self.temp_db.name)

    def tearDown(self) -> None:
        self.temp_db.close()
        os.unlink(self.temp_db.name)

    def test_emits_after_scheduled_time(self) -> None:

        async def run_test():
            fixed_now = datetime(2025, 1, 2, 9, 31, tzinfo=timezone.utc)
            source = StandupSource(now_fn=lambda tz: fixed_now.astimezone(tz))
            await self.db.upsert_task_source(
                StandupSource.id,
                True,
                source.base_priority,
                source.poll_interval_seconds,
                config={
                    "time": "09:30",
                    "timezone": "UTC"
                },
            )
            drafts = await source.poll({"db": self.db})
            self.assertEqual(len(drafts), 1)
            self.assertEqual(drafts[0].work_id, "standup:2025-01-02")

        async_to_sync(run_test)()

    def test_skips_before_scheduled_time(self) -> None:

        async def run_test():
            fixed_now = datetime(2025, 1, 2, 8, 15, tzinfo=timezone.utc)
            source = StandupSource(now_fn=lambda tz: fixed_now.astimezone(tz))
            await self.db.upsert_task_source(
                StandupSource.id,
                True,
                source.base_priority,
                source.poll_interval_seconds,
                config={
                    "time": "09:30",
                    "timezone": "UTC"
                },
            )
            drafts = await source.poll({"db": self.db})
            self.assertEqual(drafts, [])

        async_to_sync(run_test)()


if __name__ == "__main__":
    unittest.main()
