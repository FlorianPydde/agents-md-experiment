"""Shared helpers for the tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.engine import Engine  # noqa: E402
from app.models import Account, Kind, Origin, Request, Requester, Tier  # noqa: E402
from app.store import Store  # noqa: E402


def account(identifier: str = "ACC-100", balance: str = "1000.00", frozen: bool = False) -> Account:
    return Account(
        id=identifier,
        owner="Ada Lovelace",
        tier=Tier.STANDARD,
        balance=Decimal(balance),
        frozen=frozen,
    )


def request(
    reference: str = "REQ-1",
    kind: Kind = Kind.GOODWILL_CREDIT,
    amount: str = "50.00",
    account_id: str = "ACC-100",
    origin: Origin = Origin.INTERNAL,
) -> Request:
    return Request(
        reference=reference,
        kind=kind,
        account=account_id,
        amount=Decimal(amount),
        requester=Requester(name="Nia Patel", role="agent", origin=origin),
    )


class EngineCase(unittest.TestCase):
    """A test with a throwaway database and a world it can describe."""

    accounts: list[Account] = []

    def setUp(self) -> None:
        self._folder = tempfile.TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.store = Store.fresh(Path(self._folder.name) / "ledger.db")
        self.addCleanup(self.store.close)
        self.store.put_accounts(self.accounts or [account()])
        self.engine = Engine(self.store)

    def balance(self, identifier: str = "ACC-100") -> Decimal:
        return self.engine.world.account(identifier).balance

    def event_types(self, reference: str) -> list[str]:
        return [event["type"] for event in self.store.events(reference)]
