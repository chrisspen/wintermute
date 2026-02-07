"""Tests for standup source."""

from datetime import datetime, timezone

import pytest
from asgiref.sync import async_to_sync

from wintermute.sources.standup import StandupSource
from wintermute.utils import utc_now


class MockAsyncDatabase:
    """Mock database for standup source tests."""

    def __init__(self):
        self._task_sources = {}

    async def get_task_source(self, source_id):
        return self._task_sources.get(source_id)

    async def upsert_task_source(self, source_id, enabled, base_priority, poll_interval_seconds, config):
        self._task_sources[source_id] = type(
            'TaskSource',
            (),
            {
                'id': source_id,
                'enabled': enabled,
                'base_priority': base_priority,
                'poll_interval_seconds': poll_interval_seconds,
                'config_json': str(config),
                'config': config, # Provide parsed config directly
            }
        )()
        return self._task_sources[source_id]


@pytest.mark.django_db
class TestStandupSource:
    """Tests for StandupSource."""

    def test_emits_after_scheduled_time(self):
        """StandupSource should emit work item after scheduled time."""

        async def run_test():
            db = MockAsyncDatabase()
            fixed_now = datetime(2025, 1, 2, 9, 31, tzinfo=timezone.utc)
            source = StandupSource(now_fn=lambda tz: fixed_now.astimezone(tz))

            await db.upsert_task_source(
                StandupSource.id,
                True,
                source.base_priority,
                source.poll_interval_seconds,
                config={
                    "time": "09:30",
                    "timezone": "UTC"
                },
            )

            drafts = await source.poll({"db": db})
            assert len(drafts) == 1
            assert drafts[0].work_id == "standup:2025-01-02"

        async_to_sync(run_test)()

    def test_skips_before_scheduled_time(self):
        """StandupSource should not emit work item before scheduled time."""

        async def run_test():
            db = MockAsyncDatabase()
            fixed_now = datetime(2025, 1, 2, 8, 15, tzinfo=timezone.utc)
            source = StandupSource(now_fn=lambda tz: fixed_now.astimezone(tz))

            await db.upsert_task_source(
                StandupSource.id,
                True,
                source.base_priority,
                source.poll_interval_seconds,
                config={
                    "time": "09:30",
                    "timezone": "UTC"
                },
            )

            drafts = await source.poll({"db": db})
            assert drafts == []

        async_to_sync(run_test)()
