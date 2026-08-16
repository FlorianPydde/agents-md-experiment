"""`python -m app serve`

Serves the HTTP API defined in api.py using uvicorn.
"""

from __future__ import annotations

import uvicorn

from app.api import create_app

DEFAULT_DB_PATH = "log.db"


def serve_command(host: str = "127.0.0.1", port: int = 8000, db_path: str = DEFAULT_DB_PATH) -> None:
    api = create_app(db_path)
    uvicorn.run(api, host=host, port=port)
