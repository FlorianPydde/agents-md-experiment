"""HTTP API: a thin FastAPI wrapper over the engine and store."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.domain import ApprovalRecord, ApproverRole, Decision, Money, OperationName, RequestRecord
from app.engine import EngineError, apply_decision
from app.operations import OPERATIONS
from app.policy import Autonomous, NeedsApproval, PolicyContext, decide as decide_policy
from app.store import Store


class RequestOut(BaseModel):
    reference: str
    kind: str
    account: str
    amount: str
    requester_name: str
    requester_role: str
    requester_origin: str
    state: str


class ApprovalOut(BaseModel):
    id: int
    reference: str
    step_index: int
    operation: str
    required_role: str
    resolved: bool
    decision: str | None
    resolved_by_role: str | None


class LogEntryOut(BaseModel):
    id: int
    entry_type: str
    data: dict[str, str]


class DecisionIn(BaseModel):
    role: str
    decision: str


class PolicyRuleOut(BaseModel):
    operation: str
    materiality: str
    outcome: str
    role: str | None


def _request_out(record: RequestRecord) -> RequestOut:
    request = record.request
    return RequestOut(
        reference=request.reference,
        kind=request.kind.value,
        account=request.account_id,
        amount=request.amount.formatted(),
        requester_name=request.requester.name,
        requester_role=request.requester.role,
        requester_origin=request.requester.origin.value,
        state=record.state.value,
    )


def _approval_out(approval: ApprovalRecord) -> ApprovalOut:
    return ApprovalOut(
        id=approval.id,
        reference=approval.reference,
        step_index=approval.step_index,
        operation=approval.operation.value,
        required_role=approval.required_role.value,
        resolved=approval.resolved,
        decision=approval.decision.value if approval.decision is not None else None,
        resolved_by_role=approval.resolved_by_role.value if approval.resolved_by_role is not None else None,
    )


def _policy_rule_out(operation: OperationName) -> PolicyRuleOut:
    entry = OPERATIONS[operation]
    ctx = PolicyContext(operation=operation, materiality=entry.materiality, amount=Money(Decimal("0.00")))
    outcome = decide_policy(ctx)
    return PolicyRuleOut(
        operation=operation.value,
        materiality=entry.materiality.value,
        outcome="autonomous" if isinstance(outcome, Autonomous) else "needs_approval",
        role=outcome.role.value if isinstance(outcome, NeedsApproval) else None,
    )


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI(title="Governed Service Request Runner")

    def _store() -> Store:
        return Store(db_path)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/requests")
    def list_requests() -> list[RequestOut]:
        with _store() as store:
            return [_request_out(record) for record in store.list_requests()]

    @app.get("/requests/{reference}")
    def get_request(reference: str) -> RequestOut:
        with _store() as store:
            record = store.get_request(reference)
        if record is None:
            raise HTTPException(status_code=404, detail=f"unknown request reference: {reference}")
        return _request_out(record)

    @app.get("/requests/{reference}/log")
    def get_log(reference: str) -> list[LogEntryOut]:
        with _store() as store:
            if store.get_request(reference) is None:
                raise HTTPException(status_code=404, detail=f"unknown request reference: {reference}")
            entries = store.list_log(reference)
        return [LogEntryOut(id=e.id, entry_type=e.entry_type.value, data=e.data) for e in entries]

    @app.get("/approvals")
    def list_pending_approvals() -> list[ApprovalOut]:
        with _store() as store:
            return [_approval_out(a) for a in store.list_pending_approvals()]

    @app.post("/approvals/{reference}")
    def resolve_approval(reference: str, body: DecisionIn) -> RequestOut:
        try:
            role = ApproverRole(body.role)
            decision = Decision(body.decision)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        with _store() as store:
            try:
                apply_decision(store, reference, role, decision)
            except EngineError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            record = store.get_request(reference)
        assert record is not None
        return _request_out(record)

    @app.get("/operations")
    def list_operations() -> dict[str, str]:
        return {name.value: entry.materiality.value for name, entry in OPERATIONS.items()}

    @app.get("/policy")
    def list_policy() -> list[PolicyRuleOut]:
        return [_policy_rule_out(name) for name in OperationName]

    return app
