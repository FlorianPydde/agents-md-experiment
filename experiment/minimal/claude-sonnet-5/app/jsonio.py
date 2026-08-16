"""Small helper for reading JSON files with clear error messages."""

from __future__ import annotations

import json
from pathlib import Path

from app.errors import AppError


def read_json(path: str | Path):
    p = Path(path)
    if not p.is_file():
        raise AppError(f"{path}: file not found")
    try:
        with p.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise AppError(f"{path}: malformed JSON ({exc})") from None
