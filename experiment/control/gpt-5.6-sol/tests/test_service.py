from __future__ import annotations

import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.db import Database
from app.errors import AppError
from app.service import Service


WORLD = {
    "accounts": [
        {
            "id": "A-1",
            "owner": "Test Owner",
            "tier": "standard",
            "balance": "200.00",
            "frozen": False,
        },
        {
            "id": "A-2",
            "owner": "Frozen Owner",
            "tier": "premium",
            "balance": "20.00",
            "frozen": True,
        },
    ]
}


def request(
    reference: str,
    kind: str = "goodwill_credit",
    account: str = "A-1",
    amount: str = "25.00",
) -> dict[str, object]:
    return {
        "reference": reference,
        "kind": kind,
        "account": account,
        "amount": amount,
        "requester": {"name": "Agent", "role": "agent", "origin": "internal"},
    }


class ServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.temporary.name) / "test.db")
        self.service = Service(self.database)
        self.service.load_world(WORLD)

    def tearDown(self) -> None:
        self.database.close()
        self.temporary.cleanup()

    def test_policy_first_match_rules(self) -> None:
        cases = [
            ("read_account", {"account": "A-1"}, (1, None)),
            ("apply_credit", {"account": "A-1", "amount": "100.00"}, (2, None)),
            ("apply_credit", {"account": "A-1", "amount": "100.01"}, (3, "finance")),
            ("apply_debit", {"account": "A-1", "amount": "1.00"}, (4, "finance")),
            ("freeze_account", {"account": "A-1"}, (5, "risk")),
            ("unfreeze_account", {"account": "A-1"}, (5, "risk")),
            ("notify_customer", {"account": "A-1", "origin": "internal"}, (6, None)),
        ]
        for operation, arguments, expected in cases:
            with self.subTest(operation=operation, arguments=arguments):
                self.assertEqual(self.service.policy(operation, arguments), expected)

    def test_approval_resumes_and_completes_request(self) -> None:
        self.service.intake(request("R-1", amount="150.00"))
        detail = self.database.request_detail("R-1")
        self.assertEqual(detail["state"], "awaiting_approval")
        self.assertEqual(
            self.service.pending_approvals(),
            [{"reference": "R-1", "step_index": 1, "required_role": "finance"}],
        )

        self.service.decide("R-1", "finance", "approve")

        detail = self.database.request_detail("R-1")
        self.assertEqual(detail["state"], "completed")
        self.assertEqual([step["state"] for step in detail["steps"]], ["completed"] * 3)
        balance = self.database.connection.execute(
            "SELECT balance FROM accounts WHERE id = 'A-1'"
        ).fetchone()[0]
        self.assertEqual(balance, "350.00")

    def test_rejection_stops_remaining_steps(self) -> None:
        self.service.intake(request("R-2", amount="150.00"))
        self.service.decide("R-2", "finance", "reject")
        detail = self.database.request_detail("R-2")
        self.assertEqual(detail["state"], "rejected")
        self.assertEqual(
            [step["state"] for step in detail["steps"]],
            ["completed", "skipped", "skipped"],
        )

    def test_frozen_account_operation_fails_after_approval(self) -> None:
        self.service.intake(request("R-3", "collect_debt", "A-2", "10.00"))
        self.service.decide("R-3", "finance", "approve")
        detail = self.database.request_detail("R-3")
        self.assertEqual(detail["state"], "failed")
        self.assertEqual(detail["steps"][1]["state"], "failed")
        self.assertTrue(
            any(entry["event"] == "operation_failed" for entry in self.database.log_entries("R-3"))
        )

    def test_wrong_approval_role_is_rejected_without_resolving(self) -> None:
        self.service.intake(request("R-4", amount="150.00"))
        with self.assertRaisesRegex(AppError, "requires approval from finance"):
            self.service.decide("R-4", "risk", "approve")
        self.assertEqual(len(self.service.pending_approvals()), 1)

    def test_negative_amount_is_rejected(self) -> None:
        with self.assertRaisesRegex(AppError, "non-negative"):
            self.service.intake(request("R-5", amount="-0.01"))

    def test_event_log_cannot_be_changed_or_deleted(self) -> None:
        self.service.intake(request("R-6"))
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.database.connection.execute("UPDATE event_log SET event = 'changed'")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
            self.database.connection.execute("DELETE FROM event_log")


if __name__ == "__main__":
    unittest.main()
