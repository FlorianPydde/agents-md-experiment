"""Shared pytest fixtures: a temp world/scenario and a fresh Engine."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.engine import Engine


@pytest.fixture()
def world_path(tmp_path: Path) -> Path:
    path = tmp_path / "world.json"
    path.write_text(
        json.dumps(
            {
                "accounts": [
                    {
                        "id": "ACC-100",
                        "owner": "Ada Lovelace",
                        "tier": "standard",
                        "balance": "1200.00",
                        "frozen": False,
                    },
                    {
                        "id": "ACC-200",
                        "owner": "Grace Hopper",
                        "tier": "premium",
                        "balance": "50.00",
                        "frozen": True,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "log.db"


@pytest.fixture()
def engine(world_path: Path, db_path: Path) -> Engine:
    return Engine.fresh(world_path, db_path)
