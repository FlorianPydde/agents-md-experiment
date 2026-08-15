from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from app.models import (
    Account,
    ApprovalRole,
    Decision,
    Money,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Requester,
    ServiceRequest,
    Tier,
)
from app.service import Service, required_approval
from app.storage import Store


def test_policy_rules_cover_materiality_and_roles() -> None:
    assert required_approval(OperationName.READ_ACCOUNT, Money(Decimal("1"))) is None
    assert required_approval(OperationName.APPLY_CREDIT, Money(Decimal("100.00"))) is None
    assert required_approval(OperationName.APPLY_CREDIT, Money(Decimal("100.01"))) is ApprovalRole.FINANCE
    assert required_approval(OperationName.APPLY_DEBIT, Money(Decimal("1"))) is ApprovalRole.FINANCE
    assert required_approval(OperationName.UNFREEZE_ACCOUNT, Money(Decimal("1"))) is ApprovalRole.RISK


def service_with_account() -> tuple[Service, TemporaryDirectory[str]]:
    directory = TemporaryDirectory[str]()
    store = Store(Path(directory.name) / "test.sqlite3")
    store.add_account(Account("ACC-1", "Ada", Tier.STANDARD, Money(Decimal("200.00")), False))
    return Service(store), directory


def request(kind: RequestKind, amount: str) -> ServiceRequest:
    return ServiceRequest("REQ-1", kind, "ACC-1", Money(Decimal(amount)), Requester("Nia", "agent", Origin.INTERNAL))


def test_finance_approval_resumes_request_to_completion() -> None:
    service, directory = service_with_account()
    try:
        service.intake(request(RequestKind.GOODWILL_CREDIT, "150.00"))
        assert service.request_view("REQ-1")["state"] == RequestState.AWAITING_APPROVAL.value
        service.decide("REQ-1", ApprovalRole.FINANCE, Decision.APPROVE)
        assert service.request_view("REQ-1")["state"] == RequestState.COMPLETED.value
        assert service.store.get_account("ACC-1").balance.text() == "350.00"
    finally:
        service.store.close()
        directory.cleanup()


def test_insufficient_debit_fails_after_approval() -> None:
    service, directory = service_with_account()
    try:
        service.intake(request(RequestKind.COLLECT_DEBT, "500.00"))
        service.decide("REQ-1", ApprovalRole.FINANCE, Decision.APPROVE)
        view = service.request_view("REQ-1")
        assert view["state"] == RequestState.FAILED.value
        assert view["steps"][1]["state"] == "failed"
    finally:
        service.store.close()
        directory.cleanup()
