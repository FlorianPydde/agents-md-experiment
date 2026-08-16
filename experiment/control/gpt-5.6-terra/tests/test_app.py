import tempfile
import unittest
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import app.__main__ as app
from app.__main__ import Service


def request(reference, kind, account="A", amount="10.00"):
    return {"reference": reference, "kind": kind, "account": account, "amount": amount,
            "requester": {"name": "Agent", "role": "agent", "origin": "internal"}}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.tmp.name) / "test.db")
        self.service.reset({"accounts": [{"id": "A", "owner": "Owner", "tier": "standard",
                                          "balance": "200.00", "frozen": False}]})

    def tearDown(self):
        self.service.close()
        self.tmp.cleanup()

    def test_credit_policy_threshold(self):
        self.service.intake(request("small", "goodwill_credit", amount="100.00"))
        self.assertEqual(self.service.request("small")["state"], "completed")
        self.service.intake(request("large", "goodwill_credit", amount="100.01"))
        self.assertEqual(self.service.pending(), [{"reference": "large", "position": 1, "role": "finance"}])

    def test_policy_rules(self):
        self.assertIsNone(self.service._required_role("read_account", Decimal("999.00")))
        self.assertIsNone(self.service._required_role("apply_credit", Decimal("100.00")))
        self.assertEqual(self.service._required_role("apply_credit", Decimal("100.01")), "finance")
        self.assertEqual(self.service._required_role("apply_debit", Decimal("0.00")), "finance")
        self.assertEqual(self.service._required_role("freeze_account", Decimal("0.00")), "risk")
        self.assertEqual(self.service._required_role("unfreeze_account", Decimal("0.00")), "risk")
        self.assertIsNone(self.service._required_role("notify_customer", Decimal("0.00")))

    def test_approval_continues_plan(self):
        self.service.intake(request("debit", "collect_debt", amount="50.00"))
        self.service.decide("debit", "finance", "approve")
        self.assertEqual(self.service.request("debit")["state"], "completed")
        balance = self.service.db.execute("SELECT balance FROM accounts WHERE id='A'").fetchone()[0]
        self.assertEqual(Decimal(balance), Decimal("150.00"))

    def test_rejected_approval_is_final(self):
        self.service.intake(request("recovery", "account_recovery"))
        self.service.decide("recovery", "risk", "reject")
        self.assertEqual(self.service.request("recovery")["state"], "rejected")
        self.assertEqual(self.service.request("recovery")["steps"][0]["state"], "completed")
        self.assertEqual(self.service.request("recovery")["steps"][1]["state"], "pending")

    def test_failed_debit_is_final(self):
        self.service.intake(request("debit", "collect_debt", amount="500.00"))
        self.service.decide("debit", "finance", "approve")
        self.assertEqual(self.service.request("debit")["state"], "failed")
        self.assertEqual(self.service.request("debit")["steps"][1]["state"], "failed")

    def test_api_rejects_missing_approval_body(self):
        app._server_service = self.service
        response = {}
        body = b"".join(app.application(
            {"REQUEST_METHOD": "POST", "PATH_INFO": "/approvals/request",
             "CONTENT_LENGTH": "0", "wsgi.input": BytesIO()},
            lambda status, _headers: response.update(status=status),
        ))
        app._server_service = None
        self.assertEqual(response["status"], "400 Bad Request")
        self.assertIn(b"approval body is required", body)


if __name__ == "__main__":
    unittest.main()
