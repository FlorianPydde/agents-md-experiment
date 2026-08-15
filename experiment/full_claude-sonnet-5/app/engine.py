"""The engine: intake, plan, run steps under policy, and resolve approvals.

This module is the state machine described in the spec's "Request lifecycle"
and "Running a request" sections. It operates against a Database and a World,
and appends one log entry per meaningful occurrence.
"""

from __future__ import annotations

from decimal import Decimal

from app.db import Database
from app.errors import AppError
from app.operations import OperationError, World, execute
from app.policy import (
    APPROVAL_ROLES,
    DECISIONS,
    REQUEST_KINDS,
    REQUESTER_ORIGINS,
    evaluate_policy,
    plan_for,
)


def _operation_args(request_row, operation: str) -> dict:
    """Build the free-form argument dict a given operation needs from a request row."""
    return {
        "account": request_row["account"],
        "amount": request_row["amount"],
        "origin": request_row["requester_origin"],
        "reference": request_row["reference"],
    }


class Engine:
    def __init__(self, db: Database, world: World):
        self.db = db
        self.world = world
        self._arrival_counter = 0

    # -- world sync helpers ------------------------------------------------

    def _sync_account_to_db(self, account_id: str) -> None:
        account = self.world.get(account_id)
        self.db.upsert_account(
            account.id, account.owner, account.tier, account.balance, account.frozen
        )

    # -- intake --------------------------------------------------------------

    def intake(self, request: dict) -> None:
        reference = _require(request, "reference")
        kind = _require(request, "kind")
        account = _require(request, "account")
        amount = _require(request, "amount")
        requester = request.get("requester")
        if not isinstance(requester, dict):
            raise AppError(f"request {reference} is missing a requester")
        name = _require(requester, "name", context=f"request {reference} requester")
        role = _require(requester, "role", context=f"request {reference} requester")
        origin = _require(requester, "origin", context=f"request {reference} requester")

        if kind not in REQUEST_KINDS:
            raise AppError(f"request {reference} has unknown kind: {kind!r}")
        if origin not in REQUESTER_ORIGINS:
            raise AppError(f"request {reference} requester has unknown origin: {origin!r}")
        if self.db.get_request(reference) is not None:
            raise AppError(f"duplicate request reference: {reference}")

        try:
            amount_dec = Decimal(str(amount))
        except Exception as exc:
            raise AppError(f"request {reference} has an invalid amount: {amount!r}") from exc
        if amount_dec < 0:
            raise AppError(f"request {reference} has a negative amount: {amount}")

        if self.world.accounts.get(account) is None:
            raise AppError(f"request {reference} references unknown account: {account!r}")

        self._arrival_counter += 1
        self.db.insert_request(
            reference=reference,
            kind=kind,
            account=account,
            amount=str(amount_dec),
            requester_name=name,
            requester_role=role,
            requester_origin=origin,
            arrival_order=self._arrival_counter,
        )
        self.db.append_log(
            "request_received",
            reference,
            {
                "kind": kind,
                "account": account,
                "amount": str(amount_dec),
                "requester": {"name": name, "role": role, "origin": origin},
            },
        )

        steps = plan_for(kind)
        for index, operation in enumerate(steps):
            self.db.insert_step(reference, index, operation)
        self.db.append_log("plan_made", reference, {"steps": list(steps)})

        self._advance(reference)

    # -- decide --------------------------------------------------------------

    def decide(self, reference: str, role: str, decision: str) -> None:
        if role not in APPROVAL_ROLES:
            raise AppError(f"unknown approval role: {role!r}")
        if decision not in DECISIONS:
            raise AppError(f"unknown decision: {decision!r}")

        request_row = self.db.get_request(reference)
        if request_row is None:
            raise AppError(f"unknown request reference: {reference}")

        pending = self.db.pending_approval_for(reference)
        if pending is None:
            raise AppError(f"request {reference} has no pending approval")

        required_role = pending["required_role"]
        if role != required_role:
            raise AppError(
                f"decision for {reference} must come from role '{required_role}', got '{role}'"
            )

        step_index = pending["step_index"]
        self.db.resolve_approval(reference, step_index, role, decision)
        self.db.append_log(
            "approval_resolved",
            reference,
            {"step_index": step_index, "role": role, "decision": decision},
        )

        if decision == "reject":
            self.db.set_step_state(reference, step_index, "skipped")
            self.db.set_request_state(reference, "rejected")
            self.db.append_log("request_final", reference, {"state": "rejected"})
            return

        # approved: run the approved step directly (policy already decided),
        # then let the normal loop continue with subsequent steps.
        self.db.set_request_state(reference, "received")
        request_row = self.db.get_request(reference)
        ok = self._run_step(request_row, step_index)
        if not ok:
            return
        if self.db.get_request(reference)["state"] == "received":
            self._advance(reference)

    # -- internal engine loop -------------------------------------------------

    def _advance(self, reference: str) -> None:
        """Run steps from the request's current step index until it stops
        for approval, fails, completes, or is rejected."""
        request_row = self.db.get_request(reference)
        steps = self.db.steps_for(reference)
        total = len(steps)

        while True:
            request_row = self.db.get_request(reference)
            index = request_row["current_step_index"]
            if index >= total:
                self.db.set_request_state(reference, "completed")
                self.db.append_log("request_final", reference, {"state": "completed"})
                return

            operation = steps[index]["operation"]
            args = _operation_args(request_row, operation)
            decision = evaluate_policy(operation, args)
            self.db.append_log(
                "policy_decision",
                reference,
                {
                    "step_index": index,
                    "operation": operation,
                    "rule_number": decision.rule_number,
                    "needs_approval": decision.needs_approval,
                    "required_role": decision.required_role,
                },
            )

            if decision.needs_approval:
                self.db.insert_approval(reference, index, decision.required_role)
                self.db.append_log(
                    "approval_requested",
                    reference,
                    {"step_index": index, "operation": operation, "required_role": decision.required_role},
                )
                self.db.set_request_state(reference, "awaiting_approval")
                return

            ok = self._run_step(request_row, index)
            if not ok:
                return
            # after running, refresh and loop to next step (state may have advanced)
            request_row = self.db.get_request(reference)
            if request_row["state"] != "received":
                return

    def _run_step(self, request_row, step_index: int) -> bool:
        """Execute one step's operation. Returns True on success (request stays
        'received' and step index moves forward), False if the request became
        'failed'."""
        reference = request_row["reference"]
        steps = self.db.steps_for(reference)
        operation = steps[step_index]["operation"]
        args = _operation_args(request_row, operation)

        self.db.set_step_state(reference, step_index, "running")
        try:
            result = execute(operation, self.world, args)
        except OperationError as exc:
            self.db.set_step_state(reference, step_index, "failed")
            self.db.append_log(
                "operation_failed",
                reference,
                {"step_index": step_index, "operation": operation, "reason": str(exc)},
            )
            self.db.set_request_state(reference, "failed")
            self.db.append_log("request_final", reference, {"state": "failed"})
            return False

        self._sync_account_to_db(request_row["account"])
        self.db.set_step_state(reference, step_index, "done")
        self.db.append_log(
            "operation_ran",
            reference,
            {"step_index": step_index, "operation": operation, "result": result},
        )
        self.db.set_request_step_index(reference, step_index + 1)
        return True


def _require(d: dict, key: str, context: str | None = None) -> object:
    if key not in d or d[key] in (None, ""):
        where = context or "entry"
        raise AppError(f"{where} is missing required field '{key}'")
    return d[key]
