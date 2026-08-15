"""The engine: intake, plan expansion, policy gated execution, and decisions."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from . import operations, policy
from .errors import DecisionError, InputError, NotFoundError, OperationError
from .models import (
    Approval,
    ApprovalState,
    Decision,
    FINAL_STATES,
    Request,
    Role,
    State,
    Step,
    StepState,
    require,
    require_str,
)
from .plans import plan_for
from .store import Store
from .world import World


class Engine:
    """Owns the world, the requests, and the append only log."""

    def __init__(self, world: World, store: Store) -> None:
        self.world = world
        self.store = store
        self.requests: dict[str, Request] = {}
        self._order: list[str] = []
        self.store.save_accounts(self.world.sorted_accounts())

    # ------------------------------------------------------------- helpers

    def ordered_requests(self) -> list[Request]:
        return [self.requests[reference] for reference in self._order]

    def request(self, reference: str) -> Request:
        try:
            return self.requests[reference]
        except KeyError:
            raise NotFoundError(f"no request with reference {reference!r}") from None

    def _log(self, event: str, request: Request | None, **data: Any) -> None:
        self.store.append(event, request.reference if request else None, data)

    def _persist(self, request: Request) -> None:
        self.store.save_request(request)
        self.store.save_accounts(self.world.sorted_accounts())

    def pending_approvals(self) -> list[Approval]:
        return [
            approval
            for request in self.ordered_requests()
            if (approval := request.pending_approval) is not None
        ]

    # -------------------------------------------------------------- intake

    def intake(self, raw: Mapping[str, Any], label: str) -> Request:
        request = Request.from_json(raw, arrival=len(self._order), label=label)
        if request.reference in self.requests:
            raise InputError(f"duplicate request reference {request.reference!r}")
        self.world.account(request.account)

        self.requests[request.reference] = request
        self._order.append(request.reference)
        self._log(
            "request_received",
            request,
            kind=request.kind.value,
            account=request.account,
            amount=format(request.amount, ".2f"),
            requester=request.requester.to_json(),
        )

        request.steps = [
            Step(index=index, operation=name)
            for index, name in enumerate(plan_for(request.kind))
        ]
        self._log(
            "plan_created",
            request,
            steps=[step.operation for step in request.steps],
        )
        self._persist(request)
        self._advance(request)
        return request

    # ------------------------------------------------------------ decision

    def decide(self, reference: str, role: Role, decision: Decision) -> Request:
        request = self.request(reference)
        approval = request.pending_approval
        if approval is None:
            raise DecisionError(f"request {reference!r} has nothing pending approval")
        if approval.required_role is not role:
            raise DecisionError(
                f"request {reference!r} step {approval.step_index} "
                f"({approval.operation}) requires role {approval.required_role.value!r}, "
                f"but the decision came from {role.value!r}"
            )

        approval.state = (
            ApprovalState.APPROVED if decision is Decision.APPROVE else ApprovalState.REJECTED
        )
        approval.decided_by = role
        self._log(
            "approval_resolved",
            request,
            step=approval.step_index,
            operation=approval.operation,
            role=role.value,
            decision=decision.value,
        )

        if decision is Decision.REJECT:
            request.steps[approval.step_index].state = StepState.SKIPPED
            for step in request.steps[approval.step_index + 1 :]:
                step.state = StepState.SKIPPED
            self._finish(request, State.REJECTED)
            return request

        step = request.steps[approval.step_index]
        step.state = StepState.PENDING
        if self._execute(request, step):
            self._advance(request, start=approval.step_index + 1)
        return request

    # --------------------------------------------------------- run control

    def _advance(self, request: Request, start: int = 0) -> None:
        """Run steps from `start` until the plan stops, ends, or fails."""
        for step in request.steps[start:]:
            if step.state is StepState.DONE:
                continue
            operation = operations.get(step.operation)
            verdict = policy.evaluate(operation, request.amount)
            self._log(
                "policy_evaluated",
                request,
                step=step.index,
                operation=step.operation,
                **verdict.to_json(),
            )
            if not verdict.autonomous:
                assert verdict.required_role is not None
                approval = Approval(
                    reference=request.reference,
                    step_index=step.index,
                    operation=step.operation,
                    required_role=verdict.required_role,
                )
                request.approvals.append(approval)
                step.state = StepState.AWAITING_APPROVAL
                request.state = State.AWAITING_APPROVAL
                self._log(
                    "approval_requested",
                    request,
                    step=step.index,
                    operation=step.operation,
                    required_role=verdict.required_role.value,
                )
                self._persist(request)
                return
            if not self._execute(request, step):
                return
        self._finish(request, State.COMPLETED)

    def _arguments(self, request: Request, step: Step) -> dict[str, Any]:
        return {
            "account": request.account,
            "amount": format(request.amount, ".2f"),
            "origin": request.requester.origin.value,
            "reference": request.reference,
        }

    def _execute(self, request: Request, step: Step) -> bool:
        """Run one step. Returns False when the request has failed."""
        operation = operations.get(step.operation)
        args = self._arguments(request, step)
        try:
            result = operation.run(self.world, args)
        except (OperationError, InputError, NotFoundError) as exc:
            step.state = StepState.FAILED
            step.detail = str(exc)
            self._log(
                "operation_failed",
                request,
                step=step.index,
                operation=step.operation,
                reason=str(exc),
            )
            for later in request.steps[step.index + 1 :]:
                later.state = StepState.SKIPPED
            self._finish(request, State.FAILED)
            return False
        step.state = StepState.DONE
        step.detail = "; ".join(f"{key}={value}" for key, value in result.items())
        self._log(
            "operation_ran",
            request,
            step=step.index,
            operation=step.operation,
            result=result,
        )
        self._persist(request)
        return True

    def _finish(self, request: Request, state: State) -> None:
        assert state in FINAL_STATES
        request.state = state
        self._log("request_finished", request, state=state.value)
        self._persist(request)


# -------------------------------------------------------------- scenario


def replay(engine: Engine, scenario: Any, label: str = "scenario") -> None:
    """Apply an ordered scenario document to the engine."""
    if not isinstance(scenario, dict) or "steps" not in scenario:
        raise InputError(f"{label} file must be an object with a 'steps' list")
    steps = scenario["steps"]
    if not isinstance(steps, list):
        raise InputError(f"{label}.steps must be a list")

    for index, entry in enumerate(steps):
        entry_label = f"{label}.steps[{index}]"
        action = require_str(entry, "action", entry_label)
        if action == "intake":
            engine.intake(require(entry, "request", entry_label), f"{entry_label}.request")
        elif action == "decide":
            engine.decide(
                require_str(entry, "reference", entry_label),
                Role.parse(require(entry, "role", entry_label), f"{entry_label}.role"),
                Decision.parse(
                    require(entry, "decision", entry_label), f"{entry_label}.decision"
                ),
            )
        else:
            raise InputError(
                f"{entry_label}.action must be one of: intake, decide (got {action!r})"
            )
