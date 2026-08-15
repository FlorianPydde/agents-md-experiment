from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.domain import (
    Account,
    Decision,
    Origin,
    Request,
    RequestKind,
    RequestState,
    Requester,
    Role,
)
from app.service import Runner, Store, policy
from app.domain import Operation


def request(kind: RequestKind, amount: str, account: str = "A-1") -> Request:
    return Request("REQ-1", kind, account, Decimal(amount), Requester("Agent", "agent", Origin.INTERNAL))


class RunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name) / "requests.sqlite3")
        self.store.add_account(Account("A-1", "Ada", "standard", Decimal("200.00"), False))
        self.store.commit()
        self.runner = Runner(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.directory.cleanup()

    def test_policy_rules(self) -> None:
        self.assertIsNone(policy(Operation.READ_ACCOUNT, Decimal("999")))
        self.assertIsNone(policy(Operation.APPLY_CREDIT, Decimal("100.00")))
        self.assertIs(policy(Operation.APPLY_CREDIT, Decimal("100.01")), Role.FINANCE)
        self.assertIs(policy(Operation.APPLY_DEBIT, Decimal("1")), Role.FINANCE)
        self.assertIs(policy(Operation.FREEZE_ACCOUNT, Decimal("1")), Role.RISK)
        self.assertIs(policy(Operation.UNFREEZE_ACCOUNT, Decimal("1")), Role.RISK)

    def test_approval_flow_completes_after_finance_approval(self) -> None:
        self.runner.intake(request(RequestKind.GOODWILL_CREDIT, "150.00"))
        self.assertIs(self.store.request_state("REQ-1"), RequestState.AWAITING_APPROVAL)
        self.runner.decide("REQ-1", Role.FINANCE, Decision.APPROVE)
        self.assertIs(self.store.request_state("REQ-1"), RequestState.COMPLETED)
        self.assertEqual(self.store.account("A-1").balance, Decimal("350.00"))

    def test_debit_failure_finalizes_request(self) -> None:
        self.runner.intake(request(RequestKind.COLLECT_DEBT, "500.00"))
        self.runner.decide("REQ-1", Role.FINANCE, Decision.APPROVE)
        self.assertIs(self.store.request_state("REQ-1"), RequestState.FAILED)
        self.assertEqual(self.store.account("A-1").balance, Decimal("200.00"))


if __name__ == "__main__":
    unittest.main()
