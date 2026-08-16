from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from decimal import Decimal
from pathlib import Path

from app.__main__ import ApiHandler
from app.core import AppError, Engine, RequestData, policy
from http.server import ThreadingHTTPServer


ROOT = Path(__file__).resolve().parent.parent
ACCEPTANCE_OUTPUT = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


class PolicyTests(unittest.TestCase):
    def test_first_matching_rules(self) -> None:
        self.assertEqual(policy("read_account", {"account": "A"}), (1, None))
        self.assertEqual(
            policy("apply_credit", {"amount": "100.00"}), (2, None)
        )
        self.assertEqual(
            policy("apply_credit", {"amount": "100.01"}), (3, "finance")
        )
        self.assertEqual(
            policy("apply_debit", {"amount": "1.00"}), (4, "finance")
        )
        self.assertEqual(policy("freeze_account", {}), (5, "risk"))
        self.assertEqual(policy("unfreeze_account", {}), (5, "risk"))
        self.assertEqual(policy("notify_customer", {}), (6, None))


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "test.db"
        self.engine = Engine(self.database)
        self.engine.initialize()
        self.engine.load_world(
            {
                "accounts": [
                    {
                        "id": "A",
                        "owner": "Owner",
                        "tier": "standard",
                        "balance": "25.00",
                        "frozen": False,
                    },
                    {
                        "id": "F",
                        "owner": "Frozen",
                        "tier": "premium",
                        "balance": "5.00",
                        "frozen": True,
                    },
                ]
            }
        )

    def tearDown(self) -> None:
        self.engine.close()
        self.tempdir.cleanup()

    def request(
        self, reference: str, kind: str, account: str, amount: str
    ) -> RequestData:
        return RequestData(
            reference,
            kind,
            account,
            Decimal(amount),
            "Agent",
            "agent",
            "internal",
        )

    def test_approval_flow_completes_request(self) -> None:
        self.engine.intake(self.request("R1", "goodwill_credit", "A", "250.00"))
        self.assertEqual(
            self.engine.request_record("R1")["state"], "awaiting_approval"
        )
        self.assertEqual(
            self.engine.pending_approvals()[0]["required_role"], "finance"
        )
        with self.assertRaisesRegex(AppError, "requires role finance"):
            self.engine.decide("R1", "risk", "approve")
        self.engine.decide("R1", "finance", "approve")
        self.assertEqual(self.engine.request_record("R1")["state"], "completed")
        balance = self.engine.connection.execute(
            "SELECT balance FROM accounts WHERE id = 'A'"
        ).fetchone()["balance"]
        self.assertEqual(Decimal(balance), Decimal("275.00"))

    def test_frozen_account_write_fails(self) -> None:
        self.engine.intake(self.request("R2", "collect_debt", "F", "2.00"))
        self.engine.decide("R2", "finance", "approve")
        self.assertEqual(self.engine.request_record("R2")["state"], "failed")
        self.assertIn(
            "account is frozen",
            next(
                entry["data"]["error"]
                for entry in self.engine.log_entries("R2")
                if entry["event"] == "operation_failed"
            ),
        )

    def test_rejected_approval_stops_request(self) -> None:
        self.engine.intake(self.request("R3", "account_recovery", "F", "1.00"))
        self.engine.decide("R3", "risk", "reject")
        record = self.engine.request_record("R3")
        self.assertEqual(record["state"], "rejected")
        self.assertEqual(record["steps"][1]["state"], "rejected")
        self.assertEqual(record["steps"][2]["state"], "pending")


class CommandTests(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("service.db", "report.csv", "report.json", "report.tsv"):
            (ROOT / name).unlink(missing_ok=True)

    def test_acceptance_output_and_export(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "app",
                "run",
                "scenario.json",
                "--world",
                "world.json",
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.stdout, ACCEPTANCE_OUTPUT)
        subprocess.run(
            [sys.executable, "-m", "app", "export", "--format", "json"],
            cwd=ROOT,
            check=True,
        )
        report = json.loads((ROOT / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(len(report), 4)
        self.assertEqual(report[1]["reference"], "REQ-1002")


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "api.db"
        engine = Engine(self.database)
        engine.initialize()
        engine.load_world(
            {
                "accounts": [
                    {
                        "id": "A",
                        "owner": "Owner",
                        "tier": "standard",
                        "balance": "10.00",
                        "frozen": False,
                    }
                ]
            }
        )
        engine.intake(
            RequestData(
                "API-1",
                "goodwill_credit",
                "A",
                Decimal("1.00"),
                "Agent",
                "agent",
                "internal",
            )
        )
        engine.close()
        database = self.database

        class Handler(ApiHandler):
            engine_factory = staticmethod(lambda: self.open_database(database))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    @staticmethod
    def open_database(database: Path) -> Engine:
        engine = Engine(database)
        engine.initialize()
        return engine

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tempdir.cleanup()

    def get(self, path: str) -> object:
        with urllib.request.urlopen(self.base_url + path) as response:
            self.assertEqual(response.status, 200)
            return json.load(response)

    def test_api_lists_resources(self) -> None:
        self.assertEqual(self.get("/health"), {"status": "ok"})
        requests = self.get("/requests")
        self.assertEqual(requests[0]["reference"], "API-1")
        self.assertEqual(len(self.get("/operations")), 6)
        self.assertEqual(len(self.get("/policy")), 6)
        self.assertTrue(self.get("/requests/API-1/log"))


if __name__ == "__main__":
    unittest.main()

