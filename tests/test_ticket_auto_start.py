"""Tests for ticket auto-start source."""

import uuid

import pytest
from asgiref.sync import async_to_sync
from django.db.models import Max

from wintermute.models import Project, Ticket
from wintermute.sources.tickets import TicketAutoStartSource
from wintermute.utils import utc_now


class MockAsyncDatabase:
    """Mock database for auto-start source tests."""

    def __init__(self):
        self._tickets = []
        self._projects = {}
        self._task_sources = {}

    async def get_task_source(self, source_id):
        return self._task_sources.get(source_id)

    async def upsert_task_source(self, source_id, enabled, base_priority, poll_interval_seconds, config):
        self._task_sources[source_id] = type(
            'TaskSource', (), {
                'id': source_id,
                'enabled': enabled,
                'base_priority': base_priority,
                'poll_interval_seconds': poll_interval_seconds,
                'config_json': str(config),
            }
        )()
        return self._task_sources[source_id]

    async def list_auto_start_tickets(self):
        """Return tickets with auto_start=True and status='open'."""
        return [t for t in self._tickets if t.auto_start and t.status == 'open']

    def add_ticket(self, ticket):
        self._tickets.append(ticket)


@pytest.mark.django_db
class TestTicketAutoStartSource:
    """Tests for TicketAutoStartSource."""

    def test_poll_filters_auto_start_tickets(self):
        """Should only return internal tickets with auto_start=True and status=open."""

        async def run_test():
            db = MockAsyncDatabase()
            source = TicketAutoStartSource()

            await db.upsert_task_source(
                source.id,
                True,
                source.base_priority,
                source.poll_interval_seconds,
                config={},
            )

            project_id = str(uuid.uuid4())
            now = utc_now()

            # Internal ticket with auto_start (should be included)
            internal_ticket_id = str(uuid.uuid4())
            db.add_ticket(
                type(
                    'Ticket', (), {
                        'id': internal_ticket_id,
                        'project_id': project_id,
                        'title': 'Internal task',
                        'status': 'open',
                        'auto_start': True,
                        'created_at': now,
                        'updated_at': now,
                    }
                )()
            )

            # External ticket (ID contains colon - should be excluded)
            db.add_ticket(
                type(
                    'Ticket', (), {
                        'id': 'github:source:123',
                        'project_id': project_id,
                        'title': 'External task',
                        'status': 'open',
                        'auto_start': True,
                        'created_at': now,
                        'updated_at': now,
                    }
                )()
            )

            # Done ticket (should be excluded)
            db.add_ticket(
                type(
                    'Ticket', (), {
                        'id': str(uuid.uuid4()),
                        'project_id': project_id,
                        'title': 'Done task',
                        'status': 'done',
                        'auto_start': True,
                        'created_at': now,
                        'updated_at': now,
                    }
                )()
            )

            drafts = await source.poll({"db": db})
            assert len(drafts) == 1
            assert drafts[0].checkpoint.get("ticket_id") == internal_ticket_id

        async_to_sync(run_test)()
