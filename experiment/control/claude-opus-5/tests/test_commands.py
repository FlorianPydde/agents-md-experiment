"""The commands end to end, plus loading, exporting and the HTTP API."""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from support import EngineCase, request  # noqa: F401  (path setup)

from app import cli, export, loader, reporting
from app.api import Api
from app.errors import AppError
from app.store import Store

FOLDER = Path(__file__).resolve().parent.parent

ACCEPTANCE = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


class RunCommand(unittest.TestCase):
    def test_the_supplied_scenario_prints_the_acceptance_output(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            code = cli.main(
                ["run", str(FOLDER / "scenario.json"), "--world", str(FOLDER / "world.json")]
            )
        self.assertEqual(code, 0)
        self.assertEqual(captured.getvalue(), ACCEPTANCE)

    def test_running_twice_prints_the_same_text(self) -> None:
        outputs = []
        for _ in range(2):
            captured = io.StringIO()
            with redirect_stdout(captured):
                cli.main(
                    ["run", str(FOLDER / "scenario.json"), "--world", str(FOLDER / "world.json")]
                )
            outputs.append(captured.getvalue())
        self.assertEqual(outputs[0], outputs[1])


class Loading(unittest.TestCase):
    def setUp(self) -> None:
        self._folder = TemporaryDirectory()
        self.addCleanup(self._folder.cleanup)
        self.folder = Path(self._folder.name)

    def write(self, name: str, payload: object) -> Path:
        path = self.folder / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_a_missing_file_is_reported(self) -> None:
        with self.assertRaises(AppError) as caught:
            loader.load_world(self.folder / "absent.json")
        self.assertIn("file not found", str(caught.exception))

    def test_malformed_json_is_reported(self) -> None:
        path = self.folder / "broken.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(AppError):
            loader.load_world(path)

    def test_a_value_outside_the_permitted_set_is_reported(self) -> None:
        path = self.write(
            "world.json",
            {"accounts": [{"id": "A", "owner": "o", "tier": "gold", "balance": "1", "frozen": False}]},
        )
        with self.assertRaises(AppError) as caught:
            loader.load_world(path)
        self.assertIn("tier", str(caught.exception))

    def test_a_negative_amount_is_reported(self) -> None:
        path = self.write(
            "world.json",
            {"accounts": [{"id": "A", "owner": "o", "tier": "standard", "balance": "-1", "frozen": False}]},
        )
        with self.assertRaises(AppError) as caught:
            loader.load_world(path)
        self.assertIn("negative", str(caught.exception))

    def test_a_missing_field_is_reported(self) -> None:
        path = self.write(
            "world.json", {"accounts": [{"id": "A", "owner": "o", "tier": "standard", "frozen": False}]}
        )
        with self.assertRaises(AppError) as caught:
            loader.load_world(path)
        self.assertIn("balance", str(caught.exception))

    def test_an_unknown_action_is_reported(self) -> None:
        path = self.write("scenario.json", {"steps": [{"action": "teleport"}]})
        with self.assertRaises(AppError) as caught:
            loader.load_scenario(path)
        self.assertIn("action", str(caught.exception))

    def test_the_command_line_reports_errors_without_a_traceback(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            code = cli.main(["run", str(self.folder / "absent.json"), "--world", str(self.folder / "absent.json")])
        self.assertEqual(code, 1)


class ShowAndExport(EngineCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine.intake(request(amount="250.00"))

    def test_the_lookup_caches_repeated_references(self) -> None:
        lookup = reporting.Lookup(self.store)
        first = lookup.fetch("REQ-1")
        second = lookup.fetch("REQ-1")
        self.assertIs(first, second)
        self.assertEqual(lookup.hits, 1)

    def test_an_unknown_reference_is_reported(self) -> None:
        with self.assertRaises(AppError):
            reporting.Lookup(self.store).fetch("REQ-404")

    def test_the_rendering_covers_steps_approvals_and_the_log(self) -> None:
        text = reporting.render(reporting.Lookup(self.store).fetch("REQ-1"))
        for expected in ("steps", "approvals", "log", "awaiting_approval", "finance"):
            self.assertIn(expected, text)

    def test_every_format_writes_one_record_per_request(self) -> None:
        folder = Path(self._folder.name)
        for fmt in export.formats():
            with self.subTest(fmt=fmt):
                destination = export.export(self.store, fmt, folder)
                self.assertTrue(destination.exists())
                self.assertIn("REQ-1", destination.read_text(encoding="utf-8"))

    def test_an_unknown_format_is_reported(self) -> None:
        with self.assertRaises(AppError):
            export.export(self.store, "pdf", Path(self._folder.name))


class HttpApi(EngineCase):
    def setUp(self) -> None:
        super().setUp()
        self.engine.intake(request(amount="250.00"))
        self.api = Api(self.store)

    def test_health(self) -> None:
        status, payload = self.api.dispatch("GET", "/health", {})
        self.assertEqual((status, payload["status"]), (200, "ok"))

    def test_listing_and_fetching_requests(self) -> None:
        status, payload = self.api.dispatch("GET", "/requests", {})
        self.assertEqual(len(payload["requests"]), 1)
        status, payload = self.api.dispatch("GET", "/requests/REQ-1", {})
        self.assertEqual((status, payload["reference"]), (200, "REQ-1"))

    def test_fetching_an_unknown_request(self) -> None:
        status, _ = self.api.dispatch("GET", "/requests/REQ-404", {})
        self.assertEqual(status, 404)

    def test_pending_approvals_and_resolving_one(self) -> None:
        status, payload = self.api.dispatch("GET", "/approvals", {})
        self.assertEqual(payload["pending"][0]["role"], "finance")

        status, payload = self.api.dispatch(
            "POST", "/approvals/REQ-1", {"role": "finance", "decision": "approve"}
        )
        self.assertEqual((status, payload["state"]), (200, "completed"))
        self.assertEqual(self.api.dispatch("GET", "/approvals", {})[1]["pending"], [])

    def test_resolving_with_the_wrong_role(self) -> None:
        status, payload = self.api.dispatch(
            "POST", "/approvals/REQ-1", {"role": "risk", "decision": "approve"}
        )
        self.assertEqual(status, 400)
        self.assertIn("finance", payload["error"])

    def test_log_operations_and_policy(self) -> None:
        self.assertTrue(self.api.dispatch("GET", "/requests/REQ-1/log", {})[1]["log"])
        self.assertEqual(len(self.api.dispatch("GET", "/operations", {})[1]["operations"]), 6)
        self.assertEqual(len(self.api.dispatch("GET", "/policy", {})[1]["rules"]), 6)

    def test_unknown_routes_and_verbs(self) -> None:
        self.assertEqual(self.api.dispatch("GET", "/nowhere", {})[0], 404)
        self.assertEqual(self.api.dispatch("POST", "/health", {})[0], 405)


class Persistence(EngineCase):
    def test_the_log_survives_between_runs(self) -> None:
        self.engine.intake(request(amount="50.00"))
        path = self.store.path
        self.store.close()
        with Store(path) as reopened:
            self.assertTrue(reopened.events("REQ-1"))
            self.assertEqual(len(reopened.requests()), 1)


if __name__ == "__main__":
    unittest.main()
