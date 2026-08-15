"""Engine: replays scenario steps against the store, driving requests through
their plans under policy control.
"""

from __future__ import annotations

from decimal import Decimal

from app.domain import PLANS, format_amount
from app.errors import NotFoundError, ValidationError
from app.loaders import AccountSeed, DecideStep, IntakeStep
from app.operations import OPERATIONS
from app.policy import decide as policy_decide
from app.store import AccountRow, Store


def _operation_args(operation: str, request_row, requester_origin: str) -> dict:
    amount = Decimal(request_row["amount"])
    if operation in ("apply_credit", "apply_debit"):
        return {"amount": amount}
    if operation == "notify_customer":
        return {"origin": requester_origin}
    return {}


class Engine:
    """Owns the replay of a scenario against a Store."""

    def __init__(self, store: Store):
        self.store = store

    def seed_world(self, accounts: list[AccountSeed]) -> None:
        self.store.seed_accounts(accounts)

    def intake(self, step: IntakeStep) -> None:
        request = step.request
        existing = self.store.get_request(request.reference)
        if existing is not None:
            raise ValidationError(f"duplicate request reference: {request.reference}")
        if self.store.get_account(request.account) is None:
            raise ValidationError(
                f"request {request.reference} references unknown account {request.account}"
            )

        seq = self.store.append_log(
            "request_received",
            request.reference,
            {
                "kind": request.kind,
                "account": request.account,
                "amount": format_amount(request.amount),
                "requester": {
                    "name": request.requester.name,
                    "role": request.requester.role,
                    "origin": request.requester.origin,
                },
            },
        )
        self.store.create_request(request, seq)

        plan = PLANS[request.kind]
        self.store.create_plan_steps(request.reference, list(plan))
        self.store.append_log(
            "plan_created", request.reference, {"steps": list(plan)}
        )

        self._run_from(request.reference)

    def decide(self, step: DecideStep) -> None:
        request_row = self.store.get_request(step.reference)
        if request_row is None:
            raise NotFoundError(f"decide refers to unknown request: {step.reference}")

        approval = self.store.find_pending_approval(step.reference)
        if approval is None:
            raise ValidationError(
                f"decide for {step.reference} but there is no pending approval"
            )
        if approval["role"] != step.role:
            raise ValidationError(
                f"decide for {step.reference} requires role '{approval['role']}', "
                f"got '{step.role}'"
            )

        self.store.append_log(
            "approval_resolved",
            step.reference,
            {"role": step.role, "decision": step.decision, "step_index": approval["step_index"]},
        )

        if step.decision == "approve":
            self.store.resolve_approval(approval["id"], "approved")

            idx = approval["step_index"]
            request_row = self.store.get_request(step.reference)
            steps = self.store.list_steps(step.reference)
            operation_name = steps[idx]["operation"]
            operation = OPERATIONS[operation_name]
            account = self.store.get_account(request_row["account"])
            args = _operation_args(operation_name, request_row, request_row["requester_origin"])

            self._execute_step(step.reference, idx, operation, account, args)

            refreshed = self.store.get_request(step.reference)
            if refreshed["state"] == "failed":
                return

            self.store.advance_request_step(step.reference, idx + 1)
            self._run_from(step.reference)
        else:
            self.store.resolve_approval(approval["id"], "rejected")
            self.store.update_step_state(step.reference, approval["step_index"], "rejected")
            self._finish(step.reference, "rejected")

    def _finish(self, reference: str, state: str) -> None:
        self.store.update_request_state(reference, state)
        self.store.append_log("request_final", reference, {"state": state})

    def _run_from(self, reference: str) -> None:
        """Run steps starting from the request's current next_step_index,
        stopping at approval, failure, or completion."""
        while True:
            request_row = self.store.get_request(reference)
            steps = self.store.list_steps(reference)
            idx = request_row["next_step_index"]

            if idx >= len(steps):
                self._finish(reference, "completed")
                return

            step_row = steps[idx]
            operation_name = step_row["operation"]
            operation = OPERATIONS[operation_name]
            account = self.store.get_account(request_row["account"])

            args = _operation_args(operation_name, request_row, request_row["requester_origin"])

            policy_result = policy_decide(operation_name, operation.materiality, args)
            self.store.append_log(
                "policy_decision",
                reference,
                {
                    "step_index": idx,
                    "operation": operation_name,
                    "requires_approval": policy_result.requires_approval,
                    "role": policy_result.role,
                },
            )

            if policy_result.requires_approval:
                self.store.create_approval(reference, idx, policy_result.role)
                self.store.append_log(
                    "approval_requested",
                    reference,
                    {"step_index": idx, "operation": operation_name, "role": policy_result.role},
                )
                self.store.update_request_state(reference, "awaiting_approval")
                self.store.update_step_state(reference, idx, "awaiting_approval")
                return

            self._execute_step(reference, idx, operation, account, args)

            # If the step failed, execution has already finished the request.
            refreshed = self.store.get_request(reference)
            if refreshed["state"] == "failed":
                return

            self.store.advance_request_step(reference, idx + 1)

    def _execute_step(self, reference: str, idx: int, operation, account: AccountRow, args: dict) -> None:
        try:
            operation.check(account, args)
            result = operation.run(account, args)
        except Exception as exc:  # OperationFailure or unexpected
            reason = getattr(exc, "reason", str(exc))
            self.store.append_log(
                "operation_failed",
                reference,
                {"step_index": idx, "operation": operation.name, "reason": reason},
            )
            self.store.update_step_state(reference, idx, "failed")
            self._finish(reference, "failed")
            return

        self.store.save_account(account)
        self.store.append_log(
            "operation_ran",
            reference,
            {"step_index": idx, "operation": operation.name, "result": result},
        )
        self.store.update_step_state(reference, idx, "completed")
