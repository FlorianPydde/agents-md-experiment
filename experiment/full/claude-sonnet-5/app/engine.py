"""The engine: turns requests into plans and runs steps under policy control.

This is the only place that mutates account state or advances a request from
one step to the next. Every meaningful occurrence is appended to the log
before or after the corresponding action, in the order it happened.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.errors import AppError, OperationError
from app.models import Account, DecideEntry, IntakeRequest
from app.operations import run_operation
from app.plans import plan_for
from app.policy import decide
from app.storage import Storage


def _step_args(operation: str, request_row) -> dict[str, Any]:
    if operation in ("apply_credit", "apply_debit"):
        return {"amount": Decimal(request_row["amount"])}
    if operation == "notify_customer":
        return {"origin": request_row["requester_origin"]}
    return {}


class Engine:
    def __init__(self, storage: Storage):
        self.storage = storage
        self._next_seq = 0

    def load_world(self, accounts: list[Account]) -> None:
        for account in accounts:
            self.storage.insert_account(
                account.id, account.owner, account.tier, account.balance, account.frozen
            )
        self.storage.commit()

    def intake(self, request: IntakeRequest) -> None:
        if self.storage.get_request(request.reference) is not None:
            raise AppError(f"request {request.reference} already exists")
        if self.storage.get_account(request.account) is None:
            raise AppError(
                f"request {request.reference}: unknown account {request.account!r}"
            )

        seq = self._next_seq
        self._next_seq += 1

        self.storage.insert_request(
            reference=request.reference,
            seq=seq,
            kind=request.kind,
            account=request.account,
            amount=request.amount,
            requester_name=request.requester.name,
            requester_role=request.requester.role,
            requester_origin=request.requester.origin,
            state="received",
        )
        self.storage.append_log(
            "request_received",
            request.reference,
            {
                "kind": request.kind,
                "account": request.account,
                "amount": str(request.amount),
                "requester": {
                    "name": request.requester.name,
                    "role": request.requester.role,
                    "origin": request.requester.origin,
                },
            },
        )

        plan = plan_for(request.kind)
        for index, operation in enumerate(plan):
            self.storage.insert_step(request.reference, index, operation, "pending")
        self.storage.append_log(
            "plan_made", request.reference, {"steps": plan}
        )
        self.storage.commit()

        self._advance(request.reference)
        self.storage.commit()

    def decide(self, entry: DecideEntry) -> None:
        request_row = self.storage.get_request(entry.reference)
        if request_row is None:
            raise AppError(f"decide: no such request {entry.reference!r}")

        approval = self.storage.pending_approval_for_request(entry.reference)
        if approval is None:
            raise AppError(
                f"decide: request {entry.reference} has nothing pending approval"
            )
        if approval["role_required"] != entry.role:
            raise AppError(
                f"decide: request {entry.reference} requires approval from "
                f"{approval['role_required']}, not {entry.role}"
            )

        step_index = approval["step_index"]

        if entry.decision == "reject":
            self.storage.resolve_approval(approval["id"], "rejected", entry.role)
            self.storage.append_log(
                "approval_resolved",
                entry.reference,
                {
                    "step_index": step_index,
                    "role": entry.role,
                    "decision": "reject",
                },
            )
            self.storage.set_step_state(entry.reference, step_index, "skipped")
            self._finalize(entry.reference, "rejected")
            self.storage.commit()
            return

        self.storage.resolve_approval(approval["id"], "approved", entry.role)
        self.storage.append_log(
            "approval_resolved",
            entry.reference,
            {
                "step_index": step_index,
                "role": entry.role,
                "decision": "approve",
            },
        )

        if not self._run_step(entry.reference, step_index):
            self.storage.commit()
            return

        self.storage.set_request_next_step(entry.reference, step_index + 1)
        self._advance(entry.reference)
        self.storage.commit()

    def _advance(self, reference: str) -> None:
        """Run steps until the plan finishes, a step needs approval, or one
        fails."""

        request_row = self.storage.get_request(reference)
        plan = [row["operation"] for row in self.storage.steps_for_request(reference)]
        index = request_row["next_step_index"]

        while index < len(plan):
            operation = plan[index]
            request_row = self.storage.get_request(reference)
            args = _step_args(operation, request_row)
            required_role = decide(operation, args)

            self.storage.append_log(
                "policy_decided",
                reference,
                {
                    "step_index": index,
                    "operation": operation,
                    "requires_approval": required_role is not None,
                    "role": required_role,
                },
            )

            if required_role is not None:
                self.storage.insert_approval(reference, index, required_role)
                self.storage.append_log(
                    "approval_requested",
                    reference,
                    {"step_index": index, "operation": operation, "role": required_role},
                )
                self.storage.set_step_state(reference, index, "awaiting_approval")
                self.storage.set_request_state(reference, "awaiting_approval")
                self.storage.set_request_next_step(reference, index)
                return

            if not self._run_step(reference, index):
                return

            index += 1
            self.storage.set_request_next_step(reference, index)

        self._finalize(reference, "completed")

    def _run_step(self, reference: str, step_index: int) -> bool:
        """Run a single step's operation. Returns True on success, False on
        failure (in which case the request has already been finalized)."""

        request_row = self.storage.get_request(reference)
        operation = self.storage.steps_for_request(reference)[step_index]["operation"]
        args = _step_args(operation, request_row)
        account_row = self.storage.get_account(request_row["account"])
        account_state = {
            "balance": Decimal(account_row["balance"]),
            "tier": account_row["tier"],
            "frozen": bool(account_row["frozen"]),
        }

        try:
            result = run_operation(operation, account_state, args)
        except OperationError as exc:
            self.storage.append_log(
                "operation_failed",
                reference,
                {"step_index": step_index, "operation": operation, "reason": str(exc)},
            )
            self.storage.set_step_state(reference, step_index, "failed")
            self._finalize(reference, "failed")
            return False

        self.storage.update_account(
            request_row["account"], account_state["balance"], account_state["frozen"]
        )
        self.storage.append_log(
            "operation_ran",
            reference,
            {"step_index": step_index, "operation": operation, "summary": result.summary},
        )
        self.storage.set_step_state(reference, step_index, "completed")
        return True

    def _finalize(self, reference: str, state: str) -> None:
        self.storage.set_request_state(reference, state)
        self.storage.append_log("request_finalized", reference, {"state": state})
