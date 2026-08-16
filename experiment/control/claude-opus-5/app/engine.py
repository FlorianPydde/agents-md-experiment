"""The runner.

The engine holds no state of its own beyond the world it acts on: requests,
steps, approvals and the log all live in the store, so a run can be picked up
again by a later command or by the HTTP API.
"""

from __future__ import annotations

from typing import Any

from . import operations, plans, policy
from .errors import AppError, OperationFailure
from .models import Decision, Request, Role, State, Step, StepState
from .store import Store
from .world import World


class Engine:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.world = World(store.accounts())

    # -- intake --------------------------------------------------------
    def intake(self, request: Request) -> State:
        """Accept a request, plan it, and run it as far as policy allows."""
        if self.store.request(request.reference) is not None:
            raise AppError(f"request '{request.reference}' has already been received")
        if not self.world.has(request.account):
            raise AppError(
                f"request '{request.reference}' names unknown account '{request.account}'"
            )

        self.store.put_request(request, self.store.next_arrival(), State.RECEIVED)
        self.store.append_event(
            "request_received",
            request.reference,
            {
                "reference": request.reference,
                "kind": str(request.kind),
                "account": request.account,
                "amount": request.amount,
                "requester": request.requester.name,
                "role": request.requester.role,
                "origin": str(request.requester.origin),
            },
        )

        steps = plans.build(request)
        self.store.put_steps(request.reference, steps)
        self.store.append_event(
            "plan_created",
            request.reference,
            {
                "reference": request.reference,
                "kind": str(request.kind),
                "steps": [step.operation for step in steps],
            },
        )
        return self._advance(request.reference, 0)

    # -- decisions -----------------------------------------------------
    def decide(self, reference: str, role: Role, decision: Decision) -> State:
        """Resolve the pending approval on a request."""
        record = self.store.request(reference)
        if record is None:
            raise AppError(f"decision names unknown request '{reference}'")
        approval = self.store.pending_approval(reference)
        if approval is None:
            raise AppError(f"request '{reference}' has no pending approval")
        if approval.role is not role:
            raise AppError(
                f"request '{reference}' needs approval from {approval.role}"
                f" but the decision came from {role}"
            )

        self.store.resolve_approval(reference, approval.step_index, decision)
        self.store.append_event(
            "approval_resolved",
            reference,
            {
                "reference": reference,
                "step": approval.step_index,
                "operation": approval.operation,
                "role": str(role),
                "decision": str(decision),
            },
        )

        if decision is Decision.REJECT:
            self._skip_from(reference, approval.step_index)
            return self._finalise(reference, State.REJECTED, reason="rejected by " + str(role))

        step = self._step(reference, approval.step_index)
        if not self._execute(reference, step):
            return State.FAILED
        return self._advance(reference, approval.step_index + 1)

    # -- running -------------------------------------------------------
    def _advance(self, reference: str, start: int) -> State:
        steps = self.store.steps(reference)
        for record in steps[start:]:
            step = Step(
                index=record["position"],
                operation=record["operation"],
                arguments=record["arguments"],
            )
            verdict = policy.evaluate(step.operation, step.arguments)
            self.store.append_event(
                "policy_decided",
                reference,
                {
                    "reference": reference,
                    "step": step.index,
                    "operation": step.operation,
                    "rule": verdict.rule,
                    "outcome": verdict.outcome,
                    "role": str(verdict.role) if verdict.role else None,
                },
            )
            if verdict.needs_approval and verdict.role is not None:
                self.store.put_approval(reference, step.index, step.operation, verdict.role)
                self.store.set_step_state(reference, step.index, StepState.AWAITING_APPROVAL)
                self.store.set_request_state(reference, State.AWAITING_APPROVAL)
                self.store.append_event(
                    "approval_requested",
                    reference,
                    {
                        "reference": reference,
                        "step": step.index,
                        "operation": step.operation,
                        "role": str(verdict.role),
                    },
                )
                return State.AWAITING_APPROVAL
            if not self._execute(reference, step):
                return State.FAILED
        return self._finalise(reference, State.COMPLETED, reason="all steps ran")

    def _execute(self, reference: str, step: Step) -> bool:
        """Run one step. Reports whether the request may continue."""
        operation = operations.get(step.operation)
        try:
            result: dict[str, Any] = operation.run(self.world, dict(step.arguments))
        except OperationFailure as failure:
            self.store.set_step_state(reference, step.index, StepState.FAILED)
            self.store.append_event(
                "operation_failed",
                reference,
                {
                    "reference": reference,
                    "step": step.index,
                    "operation": step.operation,
                    "reason": str(failure),
                },
            )
            self._skip_from(reference, step.index + 1)
            self._finalise(reference, State.FAILED, reason=str(failure))
            return False

        self.store.put_accounts(self.world.accounts())
        self.store.set_step_state(reference, step.index, StepState.DONE)
        self.store.append_event(
            "operation_ran",
            reference,
            {
                "reference": reference,
                "step": step.index,
                "operation": step.operation,
                "materiality": str(operation.materiality),
                "result": result,
            },
        )
        return True

    def _skip_from(self, reference: str, start: int) -> None:
        for record in self.store.steps(reference)[start:]:
            if record["state"] in (str(StepState.PENDING), str(StepState.AWAITING_APPROVAL)):
                self.store.set_step_state(reference, record["position"], StepState.SKIPPED)

    def _finalise(self, reference: str, state: State, reason: str) -> State:
        self.store.set_request_state(reference, state)
        self.store.append_event(
            "request_finalised",
            reference,
            {"reference": reference, "state": str(state), "reason": reason},
        )
        return state

    def _step(self, reference: str, position: int) -> Step:
        for record in self.store.steps(reference):
            if record["position"] == position:
                return Step(
                    index=record["position"],
                    operation=record["operation"],
                    arguments=record["arguments"],
                )
        raise AppError(f"request '{reference}' has no step {position}")
