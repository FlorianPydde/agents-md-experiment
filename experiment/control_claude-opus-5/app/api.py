"""HTTP API over a database written by an earlier `run`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import operations, policy
from .engine import Engine
from .errors import AppError, NotFoundError
from .models import Decision, Role
from .store import DEFAULT_DB_NAME, Store
from .world import World


class DecisionBody(BaseModel):
    role: str
    decision: str


def load_engine(db_path: Path) -> Engine:
    """Rehydrate an engine from the store so the API sees the last run."""
    store = Store(db_path)
    world = World(store.load_accounts())
    engine = Engine(world, store)
    for request in store.load_requests():
        engine.requests[request.reference] = request
        engine._order.append(request.reference)
    return engine


def create_app(db_path: Path | None = None) -> FastAPI:
    path = db_path or (Path(__file__).resolve().parent.parent / DEFAULT_DB_NAME)
    app = FastAPI(title="Governed Service Request Runner", version="1.0.0")
    app.state.db_path = path

    def engine() -> Engine:
        return load_engine(app.state.db_path)

    def guard(func, *args: Any, **kwargs: Any) -> Any:
        try:
            return func(*args, **kwargs)
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except AppError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "database": str(app.state.db_path)}

    @app.get("/requests")
    def list_requests() -> dict[str, Any]:
        eng = engine()
        return {"requests": [r.to_json() for r in eng.ordered_requests()]}

    @app.get("/requests/{reference}")
    def get_request(reference: str) -> dict[str, Any]:
        eng = engine()
        return guard(lambda: eng.request(reference).to_json())

    @app.get("/requests/{reference}/log")
    def get_log(reference: str) -> dict[str, Any]:
        eng = engine()
        guard(lambda: eng.request(reference))
        return {
            "reference": reference,
            "entries": [entry.to_json() for entry in eng.store.log_entries(reference)],
        }

    @app.get("/approvals")
    def list_approvals() -> dict[str, Any]:
        eng = engine()
        return {"approvals": [approval.to_json() for approval in eng.pending_approvals()]}

    @app.post("/approvals/{reference}")
    def resolve_approval(reference: str, body: DecisionBody) -> dict[str, Any]:
        eng = engine()

        def apply() -> dict[str, Any]:
            role = Role.parse(body.role, "role")
            decision = Decision.parse(body.decision, "decision")
            return eng.decide(reference, role, decision).to_json()

        return guard(apply)

    @app.get("/operations")
    def list_operations() -> dict[str, Any]:
        return {"operations": [op.to_json() for op in operations.REGISTRY.values()]}

    @app.get("/policy")
    def get_policy() -> dict[str, Any]:
        return {"rules": policy.rules_json(), "roles": [role.value for role in Role]}

    return app
