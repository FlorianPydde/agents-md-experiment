import tempfile
import unittest
from pathlib import Path

from app.core import Service, policy_for
from decimal import Decimal


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.directory.name) / "test.sqlite3")
        self.service.load_world({"accounts": [
            {"id": "A1", "owner": "Owner", "tier": "standard", "balance": "150.00", "frozen": False},
        ]})

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def test_policy_rules(self):
        self.assertIsNone(policy_for("read_account", Decimal("1000")))
        self.assertIsNone(policy_for("apply_credit", Decimal("100.00")))
        self.assertEqual("finance", policy_for("apply_credit", Decimal("100.01")))
        self.assertEqual("finance", policy_for("apply_debit", Decimal("1")))
        self.assertEqual("risk", policy_for("unfreeze_account", Decimal("1")))
        self.assertIsNone(policy_for("notify_customer", Decimal("1")))

    def test_approved_credit_completes_request(self):
        self.service.intake({"reference": "R1", "kind": "goodwill_credit", "account": "A1", "amount": "120.00",
                             "requester": {"name": "Agent", "role": "agent", "origin": "internal"}})
        self.assertEqual("awaiting_approval", self.service.request_data("R1")["state"])
        self.service.decide("R1", "finance", "approve")
        result = self.service.request_data("R1")
        self.assertEqual("completed", result["state"])
        self.assertEqual(["completed"] * 3, [step["state"] for step in result["steps"]])

    def test_insufficient_debit_fails_request(self):
        self.service.intake({"reference": "R2", "kind": "collect_debt", "account": "A1", "amount": "200.00",
                             "requester": {"name": "Agent", "role": "agent", "origin": "external"}})
        self.service.decide("R2", "finance", "approve")
        result = self.service.request_data("R2")
        self.assertEqual("failed", result["state"])
        self.assertEqual("failed", result["steps"][1]["state"])


if __name__ == "__main__":
    unittest.main()
