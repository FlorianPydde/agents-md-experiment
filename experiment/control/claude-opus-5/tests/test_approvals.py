"""The approval flow: stopping, resuming, rejecting, and refusing bad decisions."""

from __future__ import annotations

import unittest
from decimal import Decimal

from support import EngineCase, account, request

from app.errors import AppError
from app.models import Decision, Kind, Origin, Role, State, StepState


class AutomaticSteps(EngineCase):
    def test_a_small_credit_completes_without_approval(self) -> None:
        state = self.engine.intake(request(amount="50.00"))
        self.assertIs(state, State.COMPLETED)
        self.assertEqual(self.balance(), Decimal("1050.00"))
        self.assertEqual(self.store.pending_approvals(), [])

    def test_the_log_records_every_occurrence(self) -> None:
        self.engine.intake(request(amount="50.00"))
        self.assertEqual(
            self.event_types("REQ-1"),
            [
                "request_received",
                "plan_created",
                "policy_decided",
                "operation_ran",
                "policy_decided",
                "operation_ran",
                "policy_decided",
                "operation_ran",
                "request_finalised",
            ],
        )

    def test_the_log_cannot_be_edited_or_deleted(self) -> None:
        self.engine.intake(request(amount="50.00"))
        for statement in ("UPDATE events SET type = 'x'", "DELETE FROM events"):
            with self.subTest(statement=statement):
                with self.assertRaises(Exception):
                    self.store.connection.execute(statement)


class WaitingForApproval(EngineCase):
    def test_a_large_credit_stops_and_records_a_pending_approval(self) -> None:
        state = self.engine.intake(request(amount="250.00"))
        self.assertIs(state, State.AWAITING_APPROVAL)
        self.assertEqual(self.balance(), Decimal("1000.00"))

        pending = self.store.pending_approvals()
        self.assertEqual(len(pending), 1)
        self.assertIs(pending[0].role, Role.FINANCE)
        self.assertEqual(pending[0].operation, "apply_credit")
        self.assertIn("approval_requested", self.event_types("REQ-1"))

    def test_approving_runs_the_step_and_continues(self) -> None:
        self.engine.intake(request(amount="250.00"))
        state = self.engine.decide("REQ-1", Role.FINANCE, Decision.APPROVE)
        self.assertIs(state, State.COMPLETED)
        self.assertEqual(self.balance(), Decimal("1250.00"))
        self.assertEqual(self.store.pending_approvals(), [])

    def test_rejecting_stops_the_request(self) -> None:
        self.engine.intake(request(amount="250.00"))
        state = self.engine.decide("REQ-1", Role.FINANCE, Decision.REJECT)
        self.assertIs(state, State.REJECTED)
        self.assertEqual(self.balance(), Decimal("1000.00"))
        states = [step["state"] for step in self.store.steps("REQ-1")]
        self.assertEqual(states, [str(StepState.DONE), str(StepState.SKIPPED), str(StepState.SKIPPED)])

    def test_a_decision_from_the_wrong_role_is_refused(self) -> None:
        self.engine.intake(request(amount="250.00"))
        with self.assertRaises(AppError) as caught:
            self.engine.decide("REQ-1", Role.RISK, Decision.APPROVE)
        self.assertIn("finance", str(caught.exception))
        self.assertEqual(len(self.store.pending_approvals()), 1)

    def test_a_decision_on_a_request_with_nothing_pending_is_refused(self) -> None:
        self.engine.intake(request(amount="50.00"))
        with self.assertRaises(AppError) as caught:
            self.engine.decide("REQ-1", Role.FINANCE, Decision.APPROVE)
        self.assertIn("no pending approval", str(caught.exception))

    def test_a_decision_on_an_unknown_request_is_refused(self) -> None:
        with self.assertRaises(AppError):
            self.engine.decide("REQ-404", Role.FINANCE, Decision.APPROVE)


class TwoApprovalsInOnePlan(EngineCase):
    accounts = [account("ACC-200", "50.00", frozen=True)]

    def test_the_run_stops_at_each_approval_in_turn(self) -> None:
        state = self.engine.intake(
            request(
                reference="REQ-2",
                kind=Kind.ACCOUNT_RECOVERY,
                amount="250.00",
                account_id="ACC-200",
                origin=Origin.EXTERNAL,
            )
        )
        self.assertIs(state, State.AWAITING_APPROVAL)
        self.assertIs(self.store.pending_approval("REQ-2").role, Role.RISK)

        state = self.engine.decide("REQ-2", Role.RISK, Decision.APPROVE)
        self.assertIs(state, State.AWAITING_APPROVAL)
        self.assertIs(self.store.pending_approval("REQ-2").role, Role.FINANCE)
        self.assertFalse(self.engine.world.account("ACC-200").frozen)

        state = self.engine.decide("REQ-2", Role.FINANCE, Decision.APPROVE)
        self.assertIs(state, State.COMPLETED)
        self.assertEqual(self.balance("ACC-200"), Decimal("300.00"))


if __name__ == "__main__":
    unittest.main()
