"""The runner: plans a request, applies policy, runs steps, resolves approvals."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from . import policy
from .domain import (
    FINAL_STATES,
    Approval,
    ApprovalState,
    Decision,
    DecisionEntry,
    EventType,
    OperationName,
    RequestRecord,
    RequestState,
    Role,
    ServiceRequest,
    Step,
    StepState,
)
from .errors import UsageError
from .loading import Scenario, load_scenario, load_world
from .money import format_amount
from .operations import Ledger, OperationFailure, operation_for
from .planner import plan_for
from .store import Store


class Engine:
    """Holds the accounts and drives requests through their plans."""

    def __init__(self, store: Store, ledger: Ledger) -> None:
        self._store = store
        self._ledger = ledger
        self._arrivals = len(store.records())

    @property
    def ledger(self) -> Ledger:
        return self._ledger

    @property
    def store(self) -> Store:
        return self._store

    # intake -------------------------------------------------------------

    def intake(self, request: ServiceRequest) -> RequestRecord:
        if self._store.record(request.reference) is not None:
            raise UsageError(f"request {request.reference} has already been received")
        record = RequestRecord(
            request=request,
            steps=plan_for(request),
            state=RequestState.RECEIVED,
            arrival=self._arrivals,
        )
        self._arrivals += 1
        self._store.save_record(record)
        self._store.append_event(
            request.reference,
            EventType.REQUEST_RECEIVED,
            (
                ("reference", request.reference),
                ("kind", request.kind.value),
                ("account", request.account),
                ("amount", format_amount(request.amount)),
                ("requester", request.requester.name),
                ("origin", request.requester.origin.value),
            ),
        )
        self._store.append_event(
            request.reference,
            EventType.PLAN_CREATED,
            tuple((f"step_{step.index}", step.operation.value) for step in record.steps),
        )
        return self._advance(record)

    # approvals ----------------------------------------------------------

    def decide(self, entry: DecisionEntry) -> RequestRecord:
        record = self._store.record(entry.reference)
        if record is None:
            raise UsageError(f"no request named {entry.reference}")
        approval = self._store.pending_approval(entry.reference)
        if approval is None:
            raise UsageError(f"request {entry.reference} has nothing pending approval")
        if approval.role is not entry.role:
            raise UsageError(
                f"request {entry.reference} needs {approval.role.value}, "
                f"not {entry.role.value}"
            )
        resolved_state = (
            ApprovalState.APPROVED
            if entry.decision is Decision.APPROVE
            else ApprovalState.REJECTED
        )
        self._store.save_approval(
            Approval(
                reference=approval.reference,
                step_index=approval.step_index,
                operation=approval.operation,
                role=approval.role,
                state=resolved_state,
            )
        )
        self._store.append_event(
            entry.reference,
            EventType.APPROVAL_RESOLVED,
            (
                ("step", str(approval.step_index)),
                ("operation", approval.operation.value),
                ("role", entry.role.value),
                ("decision", entry.decision.value),
            ),
        )
        step = record.steps[approval.step_index]
        if entry.decision is Decision.REJECT:
            record = record.with_step(step.with_state(StepState.SKIPPED))
            return self._finish(record, RequestState.REJECTED, reason="approval rejected")
        record = record.with_step(step.with_state(StepState.PENDING))
        self._store.save_record(record)
        return self._advance(record, approved_step=approval.step_index)

    # running ------------------------------------------------------------

    def _advance(self, record: RequestRecord, *, approved_step: int | None = None) -> RequestRecord:
        for step in record.steps:
            if step.state is not StepState.PENDING:
                continue
            if step.index != approved_step:
                decision = self._decide_policy(record, step)
                if not decision.runs_alone:
                    return self._pause(record, step, decision.required_role)
            outcome = self._run_step(record, step)
            if outcome is None:
                return self._finish(record.with_step(step.with_state(StepState.FAILED)), RequestState.FAILED, reason="operation failed")
            record = outcome
        return self._finish(record, RequestState.COMPLETED, reason="all steps ran")

    def _decide_policy(self, record: RequestRecord, step: Step) -> policy.PolicyDecision:
        decision = policy.evaluate(step.operation, self._step_amount(record, step))
        self._store.append_event(
            record.request.reference,
            EventType.POLICY_DECIDED,
            (
                ("step", str(step.index)),
                ("operation", step.operation.value),
                ("rule", decision.rule),
                ("outcome", "runs_alone" if decision.runs_alone else "needs_approval"),
                ("role", decision.required_role.value if decision.required_role else "none"),
            ),
        )
        return decision

    @staticmethod
    def _step_amount(record: RequestRecord, step: Step) -> Decimal:
        raw = step.arguments.get("amount")
        return record.request.amount if raw is None else Decimal(raw)

    def _pause(self, record: RequestRecord, step: Step, role: Role | None) -> RequestRecord:
        assert role is not None
        paused = record.with_step(
            Step(
                index=step.index,
                operation=step.operation,
                arguments=step.arguments,
                state=StepState.AWAITING_APPROVAL,
                required_role=role,
            )
        ).with_state(RequestState.AWAITING_APPROVAL)
        self._store.save_record(paused)
        self._store.save_approval(
            Approval(
                reference=record.request.reference,
                step_index=step.index,
                operation=step.operation,
                role=role,
                state=ApprovalState.PENDING,
            )
        )
        self._store.append_event(
            record.request.reference,
            EventType.APPROVAL_REQUESTED,
            (
                ("step", str(step.index)),
                ("operation", step.operation.value),
                ("role", role.value),
            ),
        )
        return paused

    def _run_step(self, record: RequestRecord, step: Step) -> RequestRecord | None:
        operation = operation_for(step.operation)
        prepared = operation.prepare(step.arguments)
        try:
            details = operation.run(prepared, self._ledger)
        except OperationFailure as failure:
            self._store.append_event(
                record.request.reference,
                EventType.OPERATION_FAILED,
                (
                    ("step", str(step.index)),
                    ("operation", step.operation.value),
                    ("reason", failure.reason),
                ),
            )
            return None
        self._store.save_accounts(self._ledger.sorted_accounts())
        self._store.append_event(
            record.request.reference,
            EventType.OPERATION_RAN,
            (("step", str(step.index)), ("operation", step.operation.value), *details),
        )
        done = record.with_step(step.with_state(StepState.DONE))
        self._store.save_record(done)
        return done

    def _finish(self, record: RequestRecord, state: RequestState, *, reason: str) -> RequestRecord:
        finished = record.with_state(state)
        self._store.save_record(finished)
        self._store.save_accounts(self._ledger.sorted_accounts())
        self._store.append_event(
            record.request.reference,
            EventType.REQUEST_FINISHED,
            (("state", state.value), ("reason", reason)),
        )
        return finished


def replay(scenario: Scenario, engine: Engine) -> None:
    """Apply every scenario entry in order."""
    for entry in scenario.entries:
        if isinstance(entry, DecisionEntry):
            engine.decide(entry)
        else:
            engine.intake(entry)


def run_scenario(scenario_path: Path, world_path: Path, database: Path) -> Engine:
    """Start from a clean database and replay a scenario over a fresh world."""
    accounts = load_world(world_path)
    scenario = load_scenario(scenario_path)
    store = Store(database, reset=True)
    ledger = Ledger.from_accounts(accounts)
    store.save_accounts(accounts)
    engine = Engine(store, ledger)
    replay(scenario, engine)
    return engine


__all__ = [
    "Engine",
    "FINAL_STATES",
    "OperationName",
    "replay",
    "run_scenario",
]
