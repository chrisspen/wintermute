import tempfile
import unittest
from datetime import datetime, timezone

from wintermute.db import Database
from wintermute.sources.standup import StandupSource


class StandupSourceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(delete=False)
        self.db = Database(self.temp_db.name)
        self.db.initialize()

    async def asyncTearDown(self) -> None:
        self.temp_db.close()

    async def test_emits_after_scheduled_time(self) -> None:
        fixed_now = datetime(2025, 1, 2, 9, 31, tzinfo=timezone.utc)
        source = StandupSource(now_fn=lambda tz: fixed_now.astimezone(tz))
        self.db.upsert_task_source(
            StandupSource.id,
            True,
            source.base_priority,
            source.poll_interval_seconds,
            config={"time": "09:30", "timezone": "UTC"},
        )
        drafts = await source.poll({"db": self.db})
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].work_id, "standup:2025-01-02")

    async def test_skips_before_scheduled_time(self) -> None:
        fixed_now = datetime(2025, 1, 2, 8, 15, tzinfo=timezone.utc)
        source = StandupSource(now_fn=lambda tz: fixed_now.astimezone(tz))
        self.db.upsert_task_source(
            StandupSource.id,
            True,
            source.base_priority,
            source.poll_interval_seconds,
            config={"time": "09:30", "timezone": "UTC"},
        )
        drafts = await source.poll({"db": self.db})
        self.assertEqual(drafts, [])


if __name__ == "__main__":
    unittest.main()
