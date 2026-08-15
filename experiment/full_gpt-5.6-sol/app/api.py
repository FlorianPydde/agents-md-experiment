from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .domain import AppError, NotFoundError, RequestState
from .engine import OPERATIONS, POLICY_RULES, Runner
from .presentation import request_details, request_summary
from .storage import Store
from .wire import DecisionInput


def create_app(store: Store) -> FastAPI:
    store.initialize()
    runner = Runner(store)
    app = FastAPI(title="Governed Service Request Runner")

    @app.exception_handler(AppError)
    async def handle_app_error(
        _request: object, error: AppError
    ) -> JSONResponse:
        status = 404 if isinstance(error, NotFoundError) else 409
        return JSONResponse(status_code=status, content={"detail": str(error)})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/requests")
    def list_requests() -> list[dict[str, str]]:
        return [request_summary(record) for record in store.list_requests()]

    @app.get("/requests/{reference}")
    def get_request(reference: str) -> dict[str, object]:
        return request_details(store, reference)

    @app.get("/approvals")
    def list_pending_approvals() -> list[dict[str, object]]:
        pending: list[dict[str, object]] = []
        for record in store.list_requests():
            if record.state is not RequestState.AWAITING_APPROVAL:
                continue
            approval = store.get_pending_approval(record.request.reference)
            if approval is not None:
                pending.append(
                    {
                        "reference": record.request.reference,
                        "operation": approval.operation.value,
                        "required_role": approval.required_role.value,
                    }
                )
        return pending

    @app.post("/requests/{reference}/decisions")
    def resolve_approval(
        reference: str, decision: DecisionInput
    ) -> dict[str, str]:
        runner.decide(reference, decision.role, decision.decision)
        record = store.get_request(reference)
        if record is None:
            raise RuntimeError("resolved request disappeared")
        return request_summary(record)

    @app.get("/requests/{reference}/log")
    def get_log(reference: str) -> list[dict[str, object]]:
        if store.get_request(reference) is None:
            return request_details(store, reference)["log"]
        return store.get_events(reference)

    @app.get("/operations")
    def list_operations() -> list[dict[str, str]]:
        return [
            {
                "name": name.value,
                "materiality": operation.materiality.value,
            }
            for name, operation in OPERATIONS.items()
        ]

    @app.get("/policy")
    def list_policy() -> list[dict[str, str | int]]:
        return [
            {
                "order": rule.order,
                "rule": rule.rule,
                "outcome": rule.outcome,
            }
            for rule in POLICY_RULES
        ]

    return app
