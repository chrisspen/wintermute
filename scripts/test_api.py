#!/usr/bin/env python3
"""Test access to the configured OpenAI-compatible API."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    base_url = os.environ.get("WINTERMUTE_BASE_URL")
    api_key = os.environ.get("WINTERMUTE_API_KEY")
    if not base_url or not api_key:
        print("Missing WINTERMUTE_BASE_URL or WINTERMUTE_API_KEY", file=sys.stderr)
        return 2

    url = f"{base_url.rstrip('/')}/models"
    request = urllib.request.Request(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Connection error: {exc.reason}", file=sys.stderr)
        return 1

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print("Non-JSON response received", file=sys.stderr)
        return 1

    models = payload.get("data", [])
    print(f"OK: {len(models)} models")
    for model in models[:5]:
        model_id = model.get("id", "unknown")
        print(f"- {model_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
