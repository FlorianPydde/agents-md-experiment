from decimal import Decimal

import pytest

from app.domain import Account, Money, Requester, ServiceRequest
from app.enums import Origin, RequestKind, Tier


@pytest.fixture
def store(tmp_path):
    from app.store import Store

    opened = Store(tmp_path / "test.db")
    opened.start_clean(
        (
            Account(
                id="ACC-100",
                owner="Ada Lovelace",
                tier=Tier.STANDARD,
                balance=Money(Decimal("1200.00")),
                frozen=False,
            ),
            Account(
                id="ACC-200",
                owner="Grace Hopper",
                tier=Tier.PREMIUM,
                balance=Money(Decimal("50.00")),
                frozen=True,
            ),
        )
    )
    yield opened
    opened.close()


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
        account=account,
        amount=Money(Decimal(amount)),
        requester=Requester(name="Nia Patel", role="agent", origin=origin),
    )
