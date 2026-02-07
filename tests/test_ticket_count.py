"""Unit tests for ticket count uniqueness within projects."""

import uuid

import pytest
from django.db.models import Max

from wintermute.models import Project, Ticket
from wintermute.utils import utc_now


def create_project(name: str, slug: str, symbol: str = None) -> Project:
    """Helper to create a project."""
    now = utc_now()
    return Project.objects.create(
        id=str(uuid.uuid4()),
        name=name,
        slug=slug,
        symbol=symbol or slug.upper()[:4],
        created_at=now,
        updated_at=now,
    )


def create_ticket(project_id: str, title: str) -> Ticket:
    """Helper to create a ticket with auto-incrementing count."""
    now = utc_now()
    max_count = Ticket.objects.filter(project_id=project_id).aggregate(Max('count'))['count__max']
    next_count = (max_count or 0) + 1
    return Ticket.objects.create(
        id=str(uuid.uuid4()),
        project_id=project_id,
        title=title,
        status="open",
        count=next_count,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.django_db
class TestTicketCount:
    """Tests for ticket count field uniqueness per project."""

    def test_tickets_get_sequential_counts_in_same_project(self):
        """Test that tickets in the same project get sequential counts."""
        project = create_project("Project Alpha", f"alpha-{uuid.uuid4().hex[:8]}")

        t1 = create_ticket(project.id, "First Ticket")
        t2 = create_ticket(project.id, "Second Ticket")
        t3 = create_ticket(project.id, "Third Ticket")

        assert t1.count == 1
        assert t2.count == 2
        assert t3.count == 3

    def test_tickets_in_different_projects_have_independent_counts(self):
        """Test that tickets in different projects have independent count sequences."""
        project1 = create_project("Project Alpha", f"alpha-{uuid.uuid4().hex[:8]}")
        project2 = create_project("Project Beta", f"beta-{uuid.uuid4().hex[:8]}")

        t_p1_1 = create_ticket(project1.id, "P1 First Ticket")
        t_p1_2 = create_ticket(project1.id, "P1 Second Ticket")
        t_p2_1 = create_ticket(project2.id, "P2 First Ticket")
        t_p2_2 = create_ticket(project2.id, "P2 Second Ticket")

        # Verify counts are independent per project
        assert t_p1_1.count == 1
        assert t_p1_2.count == 2
        assert t_p2_1.count == 1
        assert t_p2_2.count == 2

    def test_ticket_name_uses_project_symbol_and_count(self):
        """Test that ticket name property uses project symbol and count."""
        project = create_project("Project Alpha", f"alpha-{uuid.uuid4().hex[:8]}", symbol="ALPHA")
        ticket = create_ticket(project.id, "Test Ticket")

        assert ticket.name == "ALPHA-1"
