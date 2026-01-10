"""Ticket helpers."""

from __future__ import annotations

from typing import Optional, Tuple


def parse_issue_ticket(ticket_id: str) -> Tuple[Optional[str], Optional[str], Optional[int]]:
    if ticket_id.startswith("github:"):
        provider = "github"
    elif ticket_id.startswith("gitlab:"):
        provider = "gitlab"
    else:
        return None, None, None
    parts = ticket_id.split(":")
    if len(parts) < 3:
        return provider, None, None
    source_id = parts[1] or None
    try:
        issue_number = int(parts[2])
    except ValueError:
        issue_number = None
    return provider, source_id, issue_number
