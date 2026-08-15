from decimal import Decimal

import pytest

from app.models import (
    Account,
    AppError,
    Decision,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    Role,
    ServiceRequest,
    Tier,
    World,
)
from app.storage import Repository
from app.workflow import Runner


def request(
    reference: str,
    kind: RequestKind,
    account: str,
    amount: str,
) -> ServiceRequest:
    return ServiceRequest(
        reference=reference,
        kind=kind,
        account_id=account,
        amount=Decimal(amount),
        requester=Requester("Test User", "agent", Origin.INTERNAL),
    )


@pytest.fixture
def repository(tmp_path):
    repository = Repository(tmp_path / "test.db")
    repository.reset(
        World(
            (
                Account(
                    "OPEN",
                    "Open Owner",
                    Tier.STANDARD,
                    Decimal("200.00"),
                    False,
                ),
                Account(
                    "FROZEN",
                    "Frozen Owner",
                    Tier.PREMIUM,
                    Decimal("50.00"),
                    True,
                ),
            )
        )
    )
    yield repository
    repository.close()


def test_approval_pauses_then_resumes_request(repository):
    runner = Runner(repository)
    runner.intake(
        request("REQ-1", RequestKind.GOODWILL_CREDIT, "OPEN", "250.00")
    )

    awaiting = repository.get_request("REQ-1")
    assert awaiting is not None
    assert awaiting.state is RequestState.AWAITING_APPROVAL
    approval = repository.pending_approval("REQ-1")
    assert approval is not None
    assert approval.required_role is Role.FINANCE

    runner.decide("REQ-1", Role.FINANCE, Decision.APPROVE)

    completed = repository.get_request("REQ-1")
    assert completed is not None
    assert completed.state is RequestState.COMPLETED
    assert repository.get_account("OPEN").balance == Decimal("450.00")


def test_wrong_role_does_not_resolve_approval(repository):
    runner = Runner(repository)
    runner.intake(
        request("REQ-2", RequestKind.GOODWILL_CREDIT, "OPEN", "250.00")
    )

    with pytest.raises(AppError, match="requires role finance"):
        runner.decide("REQ-2", Role.RISK, Decision.APPROVE)

    assert repository.pending_approval("REQ-2") is not None


def test_rejection_stops_request(repository):
    runner = Runner(repository)
    runner.intake(
        request("REQ-3", RequestKind.ACCOUNT_RECOVERY, "FROZEN", "75.00")
    )
    runner.decide("REQ-3", Role.RISK, Decision.REJECT)

    stored = repository.get_request("REQ-3")
    assert stored is not None
    assert stored.state is RequestState.REJECTED
    assert repository.get_account("FROZEN").frozen is True


def test_frozen_account_causes_approved_debit_to_fail(repository):
    runner = Runner(repository)
    runner.intake(
        request("REQ-4", RequestKind.COLLECT_DEBT, "FROZEN", "10.00")
    )
    runner.decide("REQ-4", Role.FINANCE, Decision.APPROVE)

    stored = repository.get_request("REQ-4")
    assert stored is not None
    assert stored.state is RequestState.FAILED
    events = repository.list_events("REQ-4")
    assert any(event.event == "operation_failed" for event in events)
