"""HTTP API exposing health, requests, approvals, logs, and policy/operations info."""

from __future__ import annotations

from decimal import Decimal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.domain import (
    OPERATION_MATERIALITY,
    ApprovalState,
    ApproverRole,
    Decision,
    OperationName,
)
from app.engine import decide as engine_decide
from app.errors import AppError
from app.policy import POLICY_RULES
from app.store import Store


class DecisionPayload(BaseModel):
    role: str
    decision: str


def create_app(store: Store) -> FastAPI:
    fastapi_app = FastAPI(title="Governed Service Request Runner")

    @fastapi_app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @fastapi_app.get("/requests")
    def list_requests() -> list[dict]:
        return [
            {
                "reference": r.reference,
                "kind": r.kind.value,
                "account": r.account,
                "amount": str(r.amount),
                "state": r.state.value,
            }
            for r in store.all_requests_in_order()
        ]

    @fastapi_app.get("/requests/{reference}")
    def get_request(reference: str) -> dict:
        req = store.get_request(reference)
        if req is None:
            raise HTTPException(status_code=404, detail=f"unknown request reference: {reference!r}")
        steps = store.get_steps(reference)
        approvals = store.get_approvals(reference)
        return {
            "reference": req.reference,
            "kind": req.kind.value,
            "account": req.account,
            "amount": str(req.amount),
            "state": req.state.value,
            "steps": [
                {"index": s.step_index, "operation": s.operation.value, "state": s.state.value}
                for s in steps
            ],
            "approvals": [
                {
                    "step_index": a.step_index,
                    "required_role": a.required_role.value,
                    "state": a.state.value,
                    "resolved_by_role": a.resolved_by_role.value if a.resolved_by_role else None,
                }
                for a in approvals
            ],
        }

    @fastapi_app.get("/approvals")
    def list_pending_approvals() -> list[dict]:
        return [
            {
                "reference": a.reference,
                "step_index": a.step_index,
                "required_role": a.required_role.value,
                "state": a.state.value,
            }
            for a in store.all_pending_approvals()
        ]

    @fastapi_app.post("/requests/{reference}/approvals/resolve")
    def resolve_approval(reference: str, payload: DecisionPayload) -> dict:
        try:
            role = ApproverRole(payload.role)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid role: {payload.role!r}") from None
        try:
            decision_value = Decision(payload.decision)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"invalid decision: {payload.decision!r}") from None

        from app.domain import DecideEvent

        event = DecideEvent(reference=reference, role=role, decision=decision_value)
        try:
            engine_decide(store, event)
            store.commit()
        except AppError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return get_request(reference)

    @fastapi_app.get("/requests/{reference}/log")
    def get_log(reference: str) -> list[dict]:
        if store.get_request(reference) is None:
            raise HTTPException(status_code=404, detail=f"unknown request reference: {reference!r}")
        return [
            {"seq": e.seq, "kind": e.kind.value, "reference": e.reference, "data": e.data}
            for e in store.get_log(reference)
        ]

    @fastapi_app.get("/operations")
    def list_operations() -> list[dict]:
        return [
            {"name": op.value, "materiality": OPERATION_MATERIALITY[op].value}
            for op in OperationName
        ]

    @fastapi_app.get("/policy")
    def list_policy_rules() -> list[dict]:
        return [{"rule": r.label, "description": r.description} for r in POLICY_RULES]

    return fastapi_app
