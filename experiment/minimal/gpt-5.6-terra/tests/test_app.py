import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from app.__main__ import Error, Service


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.temp.name) / "test.db")
        self.service.seed({"accounts": [
            {"id": "A", "owner": "Owner", "tier": "standard", "balance": "50.00", "frozen": False}
        ]})

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def request(self, kind, value):
        return {"reference": "R", "kind": kind, "account": "A", "amount": value,
                "requester": {"name": "N", "role": "agent", "origin": "internal"}}

    def test_policy_rules(self):
        self.assertIsNone(self.service.policy("read_account", Decimal("999")))
        self.assertIsNone(self.service.policy("apply_credit", Decimal("100")))
        self.assertEqual("finance", self.service.policy("apply_credit", Decimal("100.01")))
        self.assertEqual("finance", self.service.policy("apply_debit", Decimal("1")))
        self.assertEqual("risk", self.service.policy("unfreeze_account", Decimal("1")))

    def test_approval_flow(self):
        self.service.intake(self.request("goodwill_credit", "150.00"))
        self.assertEqual("awaiting_approval", self.service.get_request("R")["state"])
        self.service.decide("R", "finance", "approve")
        self.assertEqual("completed", self.service.get_request("R")["state"])
        balance = self.service.db.execute("SELECT balance FROM accounts WHERE id='A'").fetchone()["balance"]
        self.assertEqual("200.00", balance)

    def test_failed_debit(self):
        self.service.intake(self.request("collect_debt", "60.00"))
        self.service.decide("R", "finance", "approve")
        self.assertEqual("failed", self.service.get_request("R")["state"])

    def test_wrong_approver_fails(self):
        self.service.intake(self.request("collect_debt", "1.00"))
        with self.assertRaises(Error):
            self.service.decide("R", "risk", "approve")


if __name__ == "__main__":
    unittest.main()
