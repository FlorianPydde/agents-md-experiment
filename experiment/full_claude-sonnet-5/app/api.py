"""HTTP API exposing the same data as the CLI's show/export commands.

Serves: health, list/get requests, list pending approvals, resolve an
approval, fetch log entries for a request, and the supported operations
plus policy rules.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.db import DEFAULT_DB_PATH, Database
from app.engine import Engine
from app.errors import AppError
from app.loader import load_world
from app.operations import World
from app.policy import MATERIALITY, OPERATIONS, POLICY_RULES


class DecisionBody(BaseModel):
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
        "current_step_index": row["current_step_index"],
    }


def _approval_to_dict(row) -> dict:
    return {
        "reference": row["reference"],
        "step_index": row["step_index"],
        "required_role": row["required_role"],
        "status": row["status"],
        "resolved_role": row["resolved_role"],
        "decision": row["decision"],
    }


def create_app(db_path: str = DEFAULT_DB_PATH) -> FastAPI:
    application = FastAPI(title="Governed Service Request Runner")

    def get_db() -> Database:
        return Database(db_path)

    def get_world(db: Database) -> World:
        world = World()
        for row in db.all_accounts():
            from app.operations import Account
            from decimal import Decimal

            world.accounts[row["id"]] = Account(
                id=row["id"],
                owner=row["owner"],
                tier=row["tier"],
                balance=Decimal(row["balance"]),
                frozen=bool(row["frozen"]),
            )
        return world

    @application.get("/health")
    def health():
        return {"status": "ok"}

    @application.get("/requests")
    def list_requests():
        db = get_db()
        try:
            return [_request_to_dict(row) for row in db.all_requests_in_arrival_order()]
        finally:
            db.close()

    @application.get("/requests/{reference}")
    def get_request(reference: str):
        db = get_db()
        try:
            row = db.get_request(reference)
            if row is None:
                raise HTTPException(status_code=404, detail=f"no such request: {reference}")
            return _request_to_dict(row)
        finally:
            db.close()

    @application.get("/requests/{reference}/log")
    def get_request_log(reference: str):
        db = get_db()
        try:
            if db.get_request(reference) is None:
                raise HTTPException(status_code=404, detail=f"no such request: {reference}")
            return db.log_for(reference)
        finally:
            db.close()

    @application.get("/approvals")
    def list_approvals():
        db = get_db()
        try:
            return [_approval_to_dict(row) for row in db.all_pending_approvals()]
        finally:
            db.close()

    @application.post("/requests/{reference}/decide")
    def decide(reference: str, body: DecisionBody):
        db = get_db()
        try:
            world = get_world(db)
            engine = Engine(db, world)
            try:
                engine.decide(reference, body.role, body.decision)
            except AppError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            db.commit()
            return _request_to_dict(db.get_request(reference))
        finally:
            db.close()

    @application.get("/operations")
    def list_operations():
        return [
            {"name": name, "materiality": MATERIALITY[name]} for name in OPERATIONS
        ]

    @application.get("/policy")
    def list_policy():
        return [
            {"number": rule.number, "description": rule.description} for rule in POLICY_RULES
        ]

    return application
