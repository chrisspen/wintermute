"""Entrypoint for running the admin console."""

from __future__ import annotations

import os

try:
    import uvicorn
except ImportError as exc:  # pragma: no cover - runtime dependency
    raise SystemExit("uvicorn is required to run the web console") from exc

from wintermute.web.app import create_app


def main() -> None:
    host = os.environ.get("WINTERMUTE_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WINTERMUTE_WEB_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
