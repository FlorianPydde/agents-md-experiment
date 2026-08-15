from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain import Account, AccountTier, Money
from app.storage import Store


@pytest.fixture
def store(tmp_path) -> Store:
    database = Store(tmp_path / "service.db")
    database.initialize()
    database.seed_accounts(
        [
            Account(
                account_id="ACC-1",
                owner="Test Owner",
                tier=AccountTier.STANDARD,
                balance=Money(Decimal("500.00")),
                frozen=False,
            )
        ]
    )
    return database
