from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.domain import Account, Tier
from app.engine import Engine, run_scenario
from app.loading import load_world
from app.operations import Ledger
from app.store import Store

FOLDER = Path(__file__).resolve().parent.parent


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "ledger.db"


@pytest.fixture
def engine(database: Path) -> Engine:
    accounts = (
        Account("ACC-100", "Ada Lovelace", Tier.STANDARD, Decimal("1200.00"), False),
        Account("ACC-200", "Grace Hopper", Tier.PREMIUM, Decimal("50.00"), True),
    )
    store = Store(database, reset=True)
    store.save_accounts(accounts)
    return Engine(store, Ledger.from_accounts(accounts))


@pytest.fixture
def replayed(database: Path) -> Engine:
    return run_scenario(FOLDER / "scenario.json", FOLDER / "world.json", database)


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.fixture
def world_file(tmp_path: Path) -> Path:
    return write_json(tmp_path / "world.json", json.loads((FOLDER / "world.json").read_text()))


def world_accounts() -> tuple[Account, ...]:
    return load_world(FOLDER / "world.json")
