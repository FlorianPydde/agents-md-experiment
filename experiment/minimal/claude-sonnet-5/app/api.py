"""The HTTP API served by `python -m app serve`.

Offers: health, listing/fetching requests, listing pending approvals,
resolving a pending approval, fetching a request's log entries, and listing
the supported operations and policy rules.
"""

from __future__ import annotations

import threading

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.domain import APPROVER_ROLES, MATERIALITY, OPERATIONS, list_policy_rules
from app.engine import Engine
from app.errors import AppError
from app.scenario import DecideAction

DEFAULT_DB_PATH = "log.db"


class DecisionPayload(BaseModel):
    role: str
    decision: str


def _request_to_dict(row) -> dict:
    return {
        "reference": row["reference"],
        "kind": row["kind"],
        "account": row["account"],
        "amount": row["amount"],
        "requester": {
            "name": row["requester_name"],
            "role": row["requester_role"],
            "origin": row["requester_origin"],
        },
        "state": row["state"],
    }


def _approval_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "reference": row["request_reference"],
        "step_index": row["step_index"],
        "role_required": row["role_required"],
        "status": row["status"],
        "decided_role": row["decided_role"],
        "decision": row["decision"],
    }


def create_app(db_path: str = DEFAULT_DB_PATH) -> FastAPI:
    api = FastAPI(title="Governed Service Request Runner")
    eng = Engine.open_existing(db_path)
    lock = threading.Lock()

    @api.get("/health")
    def health():
        return {"status": "ok"}

    @api.get("/requests")
    def list_requests():
        with lock:
            return [_request_to_dict(row) for row in eng.list_requests()]

    @api.get("/requests/{reference}")
    def get_request(reference: str):
        with lock:
            row = eng.get_request(reference)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no such request '{reference}'")
        return _request_to_dict(row)

    @api.get("/requests/{reference}/log")
    def get_request_log(reference: str):
        with lock:
            row = eng.get_request(reference)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no such request '{reference}'")
            return eng.get_log(reference)

    @api.get("/approvals")
    def list_pending_approvals():
        with lock:
            return [_approval_to_dict(row) for row in eng.list_pending_approvals()]

    @api.post("/requests/{reference}/decide")
    def decide(reference: str, payload: DecisionPayload):
        if payload.decision not in ("approve", "reject"):
            raise HTTPException(
                status_code=400, detail=f"unknown decision '{payload.decision}'"
            )
        with lock:
            try:
                eng.decide(
                    DecideAction(reference=reference, role=payload.role, decision=payload.decision)
                )
            except AppError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return _request_to_dict(eng.get_request(reference))

    @api.get("/operations")
    def list_operations():
        return [{"name": op, "materiality": MATERIALITY[op]} for op in OPERATIONS]

    @api.get("/policy")
    def get_policy():
        return {"approver_roles": list(APPROVER_ROLES), "rules": list_policy_rules()}

    return api
