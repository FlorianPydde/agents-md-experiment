from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.domain import Account, Requester, ServiceRequest, World  # noqa: E402
from app.engine import Runner  # noqa: E402
from app.enums import Origin, RequestKind, Tier  # noqa: E402
from app.store import Store  # noqa: E402
from app.values import Money  # noqa: E402


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "test.sqlite3")
    yield s
    s.close()


@pytest.fixture
def world() -> World:
    return World(
        (
            Account("ACC-100", "Ada Lovelace", Tier.STANDARD, Money.parse("1200.00"), False),
            Account("ACC-200", "Grace Hopper", Tier.PREMIUM, Money.parse("50.00"), True),
        )
    )


@pytest.fixture
def runner(store: Store, world: World) -> Runner:
    store.save_world(world)
    return Runner(store, world)


def make_request(
    reference: str,
    kind: RequestKind,
    account: str,
    amount: str,
    origin: Origin = Origin.INTERNAL,
) -> ServiceRequest:
    return ServiceRequest(
        reference=reference,
        kind=kind,
        account_id=account,
        amount=Money.parse(amount),
        requester=Requester("Nia Patel", "agent", origin),
    )
