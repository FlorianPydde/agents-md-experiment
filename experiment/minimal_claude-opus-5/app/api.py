"""HTTP API over the persisted store."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .domain import Decision, DecisionEntry, Role
from .engine import Engine
from .errors import AppError
from .operations import OPERATIONS
from .policy import RULES
from .store import ApprovalRecord, RequestRecord, RequestView, Store, default_db_path
from .world import World


class ResolveBody(BaseModel):
    role: Role
    decision: Decision


def _request_payload(record: RequestRecord) -> dict[str, Any]:
    return {
        "reference": record.reference,
        "kind": record.kind.value,
        "state": record.state.value,
        "account": record.account,
        "amount": f"{record.amount:.2f}",
        "requester": {
            "name": record.requester_name,
            "role": record.requester_role,
            "origin": record.requester_origin.value,
        },
    }


def _approval_payload(record: ApprovalRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "reference": record.reference,
        "step": record.position,
        "operation": record.operation.value,
        "required_role": record.required_role.value,
        "state": record.state.value,
        "resolved_by": record.resolved_by.value if record.resolved_by else None,
        "decision": record.decision.value if record.decision else None,
    }


def _view_payload(view: RequestView) -> dict[str, Any]:
    return {
        **_request_payload(view.request),
        "steps": [
            {"position": s.position, "operation": s.operation.value, "state": s.state.value}
            for s in view.steps
        ],
        "approvals": [_approval_payload(a) for a in view.approvals],
    }


def create_app(db_path: Path | None = None) -> FastAPI:
    path = db_path or default_db_path()
    api = FastAPI(title="Governed Service Request Runner")

    def open_store() -> Store:
        return Store(path)

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/requests")
    def list_requests() -> list[dict[str, Any]]:
        store = open_store()
        try:
            return [_request_payload(r) for r in store.all_requests()]
        finally:
            store.close()

    @api.get("/requests/{reference}")
    def get_request(reference: str) -> dict[str, Any]:
        store = open_store()
        try:
            return _view_payload(store.view_request(reference))
        except AppError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        finally:
            store.close()

    @api.get("/requests/{reference}/log")
    def get_log(reference: str) -> list[dict[str, Any]]:
        store = open_store()
        try:
            view = store.view_request(reference)
        except AppError as error:
            raise HTTPException(status_code=404, detail=str(error)) from None
        finally:
            store.close()
        return [
            {"id": e.id, "kind": e.kind.value, "detail": [list(pair) for pair in e.detail]}
            for e in view.log
        ]

    @api.get("/approvals")
    def list_approvals() -> list[dict[str, Any]]:
        store = open_store()
        try:
            return [_approval_payload(a) for a in store.pending_approvals()]
        finally:
            store.close()

    @api.post("/approvals/{reference}")
    def resolve(reference: str, body: ResolveBody) -> dict[str, Any]:
        store = open_store()
        try:
            world = World(store.load_accounts())
            Engine(store, world).decide(
                DecisionEntry(reference=reference, role=body.role, decision=body.decision)
            )
            store.save_accounts(world.sorted_accounts())
            return _view_payload(store.view_request(reference))
        except AppError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        finally:
            store.close()

    @api.get("/operations")
    def list_operations() -> list[dict[str, str]]:
        return [
            {"name": op.name.value, "materiality": op.materiality.value}
            for op in OPERATIONS.values()
        ]

    @api.get("/policy")
    def list_policy() -> list[dict[str, Any]]:
        return [
            {
                "number": rule.number,
                "description": rule.description,
                "required_role": rule.required_role.value if rule.required_role else None,
            }
            for rule in RULES
        ]

    return api
