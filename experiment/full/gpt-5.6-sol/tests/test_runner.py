from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from app.core import AppError, Engine, Store, policy_for


def world(*, balance: str = "500.00", frozen: bool = False) -> dict:
    return {
        "accounts": [
            {
                "id": "A-1",
                "owner": "Test Owner",
                "tier": "standard",
                "balance": balance,
                "frozen": frozen,
            }
        ]
    }


def request(
    kind: str = "goodwill_credit", amount: str = "50.00"
) -> dict:
    return {
        "reference": "R-1",
        "kind": kind,
        "account": "A-1",
        "amount": amount,
        "requester": {
            "name": "Test Agent",
            "role": "agent",
            "origin": "internal",
        },
    }


class StoreTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary_directory.name) / "test.sqlite3")
        self.store.reset()
        self.engine = Engine(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()


class PolicyTests(unittest.TestCase):
    def test_first_matching_policy_rules(self) -> None:
        self.assertIsNone(policy_for("read_account", Decimal("999.00")))
        self.assertIsNone(policy_for("apply_credit", Decimal("100.00")))
        self.assertEqual(
            policy_for("apply_credit", Decimal("100.01")), "finance"
        )
        self.assertEqual(policy_for("apply_debit", Decimal("1.00")), "finance")
        self.assertEqual(policy_for("freeze_account", Decimal("0.00")), "risk")
        self.assertEqual(
            policy_for("unfreeze_account", Decimal("0.00")), "risk"
        )
        self.assertIsNone(policy_for("notify_customer", Decimal("0.00")))


class ApprovalFlowTests(StoreTestCase):
    def test_approved_step_resumes_and_completes_request(self) -> None:
        self.engine.load_world(world())
        self.engine.intake(request(amount="250.00"))

        details = self.store.request_details("R-1")
        self.assertEqual(details["state"], "awaiting_approval")
        self.assertEqual(details["approvals"][0]["required_role"], "finance")

        self.engine.decide("R-1", "finance", "approve")

        details = self.store.request_details("R-1")
        self.assertEqual(details["state"], "completed")
        self.assertTrue(all(step["state"] == "completed" for step in details["steps"]))
        balance = self.store.connection.execute(
            "SELECT balance FROM accounts WHERE id = 'A-1'"
        ).fetchone()[0]
        self.assertEqual(balance, "750.00")

    def test_rejection_stops_request(self) -> None:
        self.engine.load_world(world(frozen=True))
        self.engine.intake(request(kind="account_recovery", amount="25.00"))

        self.engine.decide("R-1", "risk", "reject")

        details = self.store.request_details("R-1")
        self.assertEqual(details["state"], "rejected")
        self.assertEqual(details["steps"][1]["state"], "rejected")
        self.assertEqual(details["steps"][2]["state"], "pending")

    def test_wrong_role_is_rejected(self) -> None:
        self.engine.load_world(world())
        self.engine.intake(request(amount="250.00"))

        with self.assertRaisesRegex(AppError, "requires role finance"):
            self.engine.decide("R-1", "risk", "approve")


class OperationFailureTests(StoreTestCase):
    def test_insufficient_balance_fails_debit(self) -> None:
        self.engine.load_world(world(balance="20.00"))
        self.engine.intake(request(kind="collect_debt", amount="50.00"))

        self.engine.decide("R-1", "finance", "approve")

        details = self.store.request_details("R-1")
        self.assertEqual(details["state"], "failed")
        self.assertEqual(details["steps"][1]["state"], "failed")
        event_names = [entry["event"] for entry in details["log"]]
        self.assertIn("operation_failed", event_names)

    def test_frozen_account_blocks_writes_except_unfreeze(self) -> None:
        self.engine.load_world(world(frozen=True))
        self.engine.intake(request())

        details = self.store.request_details("R-1")
        self.assertEqual(details["state"], "failed")
        self.assertEqual(details["steps"][1]["state"], "failed")


if __name__ == "__main__":
    unittest.main()
