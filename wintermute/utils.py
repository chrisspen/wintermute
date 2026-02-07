"""Utility functions for Wintermute."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def utc_now() -> str:
    """Return current UTC time as ISO format string."""
    return datetime.now(timezone.utc).isoformat()


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


def json_dumps(value: Any) -> str:
    """Serialize value to compact JSON string."""
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def json_loads(value: Optional[str]) -> Any:
    """Deserialize JSON string to value, returning empty dict for None/empty."""
    if not value:
        return {}
    return json.loads(value)
