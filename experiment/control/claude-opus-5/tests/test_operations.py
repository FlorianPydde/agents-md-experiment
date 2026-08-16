"""Operations that fail, and the state they leave behind."""

from __future__ import annotations

import unittest
from decimal import Decimal

from support import EngineCase, account, request

from app.errors import AppError, OperationFailure
from app.models import Decision, Kind, Origin, Role, State
from app.operations import MESSAGE_TEMPLATES, catalogue, get


class FailingOperations(EngineCase):
    accounts = [account("ACC-200", "50.00", frozen=False)]

    def test_a_debit_beyond_the_balance_fails_the_request(self) -> None:
        self.engine.intake(
            request(reference="REQ-3", kind=Kind.COLLECT_DEBT, amount="500.00", account_id="ACC-200")
        )
        state = self.engine.decide("REQ-3", Role.FINANCE, Decision.APPROVE)
        self.assertIs(state, State.FAILED)
        self.assertEqual(self.balance("ACC-200"), Decimal("50.00"))
        self.assertIn("operation_failed", self.event_types("REQ-3"))

    def test_no_further_step_runs_after_a_failure(self) -> None:
        self.engine.intake(
            request(reference="REQ-3", kind=Kind.COLLECT_DEBT, amount="500.00", account_id="ACC-200")
        )
        self.engine.decide("REQ-3", Role.FINANCE, Decision.APPROVE)
        states = [step["state"] for step in self.store.steps("REQ-3")]
        self.assertEqual(states, ["done", "failed", "skipped"])


class FrozenAccounts(EngineCase):
    accounts = [account("ACC-200", "500.00", frozen=True)]

    def test_a_write_on_a_frozen_account_fails(self) -> None:
        self.engine.intake(request(reference="REQ-4", amount="50.00", account_id="ACC-200"))
        self.assertIs(self.store.request("REQ-4")[1], State.FAILED)
        self.assertEqual(self.balance("ACC-200"), Decimal("500.00"))

    def test_unfreezing_is_allowed_on_a_frozen_account(self) -> None:
        get("unfreeze_account").run(self.engine.world, {"account": "ACC-200"})
        self.assertFalse(self.engine.world.account("ACC-200").frozen)


class OperationArguments(EngineCase):
    def test_a_missing_argument_is_refused(self) -> None:
        with self.assertRaises(AppError):
            get("apply_credit").run(self.engine.world, {"account": "ACC-100"})

    def test_a_negative_amount_is_refused(self) -> None:
        with self.assertRaises(AppError):
            get("apply_credit").run(
                self.engine.world, {"account": "ACC-100", "amount": Decimal("-1.00")}
            )

    def test_an_unknown_account_is_refused(self) -> None:
        with self.assertRaises(AppError):
            get("read_account").run(self.engine.world, {"account": "ACC-999"})

    def test_an_unknown_operation_is_refused(self) -> None:
        with self.assertRaises(AppError):
            get("teleport_funds")

    def test_a_debit_reports_why_it_failed(self) -> None:
        with self.assertRaises(OperationFailure):
            get("apply_debit").run(
                self.engine.world, {"account": "ACC-100", "amount": Decimal("5000.00")}
            )

    def test_notify_uses_the_template_for_the_origin(self) -> None:
        for origin in Origin:
            with self.subTest(origin=origin):
                result = get("notify_customer").run(
                    self.engine.world,
                    {"account": "ACC-100", "reference": "REQ-1", "origin": str(origin)},
                )
                self.assertEqual(
                    result["message"],
                    MESSAGE_TEMPLATES[origin].format(reference="REQ-1", account="ACC-100"),
                )

    def test_the_catalogue_lists_six_operations(self) -> None:
        self.assertEqual(len(catalogue()), 6)


if __name__ == "__main__":
    unittest.main()
