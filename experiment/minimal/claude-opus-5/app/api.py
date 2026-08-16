"""The HTTP API over the same engine, store and policy the commands use."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from . import policy
from .domain import (
    Approval,
    Decision,
    DecisionEntry,
    Event,
    RequestRecord,
    Role,
    parse_member,
)
from .engine import Engine
from .errors import AppError
from .money import format_amount
from .operations import OPERATIONS, Ledger
from .store import DEFAULT_DATABASE, Store


class ResolveBody(BaseModel):
    role: str
    decision: str


def _request_json(record: RequestRecord) -> dict[str, Any]:
    return {
        "reference": record.request.reference,
        "kind": record.request.kind.value,
        "account": record.request.account,
        "amount": format_amount(record.request.amount),
        "requester": {
            "name": record.request.requester.name,
            "role": record.request.requester.role,
            "origin": record.request.requester.origin.value,
        },
        "state": record.state.value,
        "steps": [
            {
                "index": step.index,
                "operation": step.operation.value,
                "state": step.state.value,
                "required_role": step.required_role.value if step.required_role else None,
            }
            for step in record.steps
        ],
    }


def _approval_json(approval: Approval) -> dict[str, Any]:
    return {
        "reference": approval.reference,
        "step": approval.step_index,
        "operation": approval.operation.value,
        "role": approval.role.value,
        "state": approval.state.value,
    }


def _event_json(event: Event) -> dict[str, Any]:
    return {
        "sequence": event.sequence,
        "reference": event.reference,
        "type": event.type.value,
        "details": [{"key": key, "value": value} for key, value in event.details],
    }


def create_app(database: Path = DEFAULT_DATABASE) -> FastAPI:
    """Build the API. Each call opens its own connection to the store."""
    app = FastAPI(title="Governed service request runner")

    def store() -> Iterator[Store]:
        opened = Store(database)
        try:
            yield opened
        finally:
            opened.close()

    def _record_or_404(opened: Store, reference: str) -> RequestRecord:
        record = opened.record(reference)
        if record is None:
            raise HTTPException(status_code=404, detail=f"no request named {reference}")
        return record

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "database": str(database)}

    @app.get("/requests")
    def list_requests(opened: Store = Depends(store)) -> list[dict[str, Any]]:
        return [_request_json(record) for record in opened.records()]

    @app.get("/requests/{reference}")
    def get_request(reference: str, opened: Store = Depends(store)) -> dict[str, Any]:
        return _request_json(_record_or_404(opened, reference))

    @app.get("/requests/{reference}/log")
    def get_log(reference: str, opened: Store = Depends(store)) -> list[dict[str, Any]]:
        _record_or_404(opened, reference)
        return [_event_json(event) for event in opened.events_for(reference)]

    @app.get("/approvals")
    def list_pending(opened: Store = Depends(store)) -> list[dict[str, Any]]:
        return [_approval_json(approval) for approval in opened.pending_approvals()]

    @app.post("/approvals/{reference}")
    def resolve(reference: str, body: ResolveBody, opened: Store = Depends(store)) -> dict[str, Any]:
        _record_or_404(opened, reference)
        try:
            entry = DecisionEntry(
                reference=reference,
                role=parse_member(Role, body.role, field="role"),
                decision=parse_member(Decision, body.decision, field="decision"),
            )
            engine = Engine(opened, Ledger.from_accounts(opened.accounts()))
            record = engine.decide(entry)
        except AppError as error:
            raise HTTPException(status_code=400, detail=str(error)) from None
        return _request_json(record)

    @app.get("/operations")
    def list_operations() -> list[dict[str, str]]:
        return [
            {
                "name": name.value,
                "materiality": operation.materiality.value,
                "effect": operation.description,
            }
            for name, operation in OPERATIONS.items()
        ]

    @app.get("/policy")
    def list_policy() -> list[dict[str, Any]]:
        return [
            {
                "name": rule.name,
                "describes": rule.describes,
                "required_role": rule.required_role.value if rule.required_role else None,
            }
            for rule in policy.RULES
        ]

    return app
