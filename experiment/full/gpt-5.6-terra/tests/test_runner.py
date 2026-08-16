import tempfile
import unittest
from pathlib import Path

from app.main import Service, amount


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.directory.name) / "test.db")
        self.service.create_schema()
        self.service.load_world({"accounts": [
            {"id": "A", "owner": "A", "tier": "standard", "balance": "100.00", "frozen": False}
        ]})

    def tearDown(self):
        self.service.close()
        self.directory.cleanup()

    def request(self, kind, value):
        return {"reference": "R", "kind": kind, "account": "A", "amount": value,
                "requester": {"name": "N", "role": "agent", "origin": "internal"}}

    def test_credit_policy_threshold(self):
        self.service.intake(self.request("goodwill_credit", "100.00"))
        self.assertEqual("completed", self.service.request_row("R")["state"])

    def test_approval_flow(self):
        self.service.intake(self.request("goodwill_credit", "100.01"))
        self.assertEqual("awaiting_approval", self.service.request_row("R")["state"])
        self.service.decide("R", "finance", "approve")
        self.assertEqual("completed", self.service.request_row("R")["state"])

    def test_failing_debit(self):
        self.service.intake(self.request("collect_debt", "101.00"))
        self.service.decide("R", "finance", "approve")
        self.assertEqual("failed", self.service.request_row("R")["state"])

    def test_rejected_recovery_leaves_account_frozen(self):
        self.service.db.execute("UPDATE accounts SET frozen=1 WHERE id='A'")
        self.service.db.commit()
        self.service.intake(self.request("account_recovery", "10.00"))
        self.service.decide("R", "risk", "reject")
        self.assertEqual("rejected", self.service.request_row("R")["state"])
        self.assertTrue(self.service.db.execute("SELECT frozen FROM accounts WHERE id='A'").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
