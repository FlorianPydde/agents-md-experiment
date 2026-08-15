"""HTTP API over the stored run."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse

from .domain import Approval, RequestRecord
from .engine import Runner
from .errors import AppError
from .export import ReportRow
from .operations import OPERATIONS
from .policy import RULES
from .store import DEFAULT_DB, LogEntry, Store
from .wire import DecisionBody


def _request_payload(record: RequestRecord) -> dict[str, object]:
    payload = ReportRow.of(record).as_mapping()
    payload["steps"] = [
        {"index": s.index, "operation": s.operation.value, "state": s.state.value}
        for s in record.steps
    ]
    payload["approvals"] = [_approval_payload(a) for a in record.approvals]
    return payload


def _approval_payload(approval: Approval) -> dict[str, object]:
    return {
        "reference": approval.reference,
        "step": approval.step_index,
        "operation": approval.operation.value,
        "required_role": approval.required_role.value,
        "resolution": None if approval.resolution is None else approval.resolution.value,
    }


def _log_payload(entry: LogEntry) -> dict[str, object]:
    return {
        "id": entry.id,
        "reference": entry.reference,
        "kind": entry.kind.value,
        "details": [list(d) for d in entry.details],
    }


def create_app(db_path: Path = DEFAULT_DB) -> FastAPI:
    api = FastAPI(title="Governed Service Request Runner")
    store = Store(db_path)

    @api.exception_handler(AppError)
    async def _app_error(_request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @api.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/requests")
    def list_requests() -> list[dict[str, object]]:
        return [_request_payload(r) for r in store.all_requests()]

    @api.get("/requests/{reference}")
    def get_request(reference: str) -> dict[str, object]:
        try:
            return _request_payload(store.load_request(reference))
        except AppError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.get("/requests/{reference}/log")
    def get_log(reference: str) -> list[dict[str, object]]:
        try:
            store.load_request(reference)
        except AppError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return [_log_payload(e) for e in store.log_for(reference)]

    @api.get("/approvals")
    def list_approvals() -> list[dict[str, object]]:
        return [_approval_payload(a) for a in store.pending_approvals()]

    @api.post("/approvals/{reference}")
    def resolve_approval(reference: str, body: DecisionBody) -> dict[str, object]:
        runner = Runner(store, store.load_world())
        record = runner.resolve(reference, body.role, body.decision)
        return _request_payload(record)

    @api.get("/operations")
    def list_operations() -> list[dict[str, str]]:
        return [
            {"name": name.value, "materiality": op.materiality.value}
            for name, op in OPERATIONS.items()
        ]

    @api.get("/policy")
    def list_policy() -> list[dict[str, object]]:
        return [
            {
                "order": i,
                "rule": rule.description,
                "required_role": (
                    None if rule.required_role is None else rule.required_role.value
                ),
            }
            for i, rule in enumerate(RULES, start=1)
        ]

    return api
