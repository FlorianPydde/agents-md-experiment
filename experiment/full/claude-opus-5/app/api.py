"""The HTTP API. Wire models in, wire models out, domain objects in between."""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.domain import Approval, TrackedRequest
from app.engine import decide
from app.enums import EventKind, Materiality, OperationName, RequestState, Role
from app.errors import ServiceRequestError, UnknownReferenceError
from app.operations import OPERATIONS
from app.policy import POLICY_RULES
from app.store import Store
from app.views import request_view
from app.wire import DecisionBody


class HealthOut(BaseModel):
    status: str


class RequestOut(BaseModel):
    reference: str
    kind: str
    state: RequestState
    account: str
    amount: str


class StepOut(BaseModel):
    position: int
    operation: OperationName
    state: str


class ApprovalOut(BaseModel):
    reference: str
    position: int
    role: Role
    state: str


class RequestDetailOut(BaseModel):
    request: RequestOut
    steps: list[StepOut]
    approvals: list[ApprovalOut]


class LogEntryOut(BaseModel):
    position: int
    kind: EventKind
    reference: str
    details: dict[str, str]


class OperationOut(BaseModel):
    name: OperationName
    materiality: Materiality


class PolicyRuleOut(BaseModel):
    order: int
    describe: str
    required_role: Role | None


def build_api(database: Path) -> FastAPI:
    """Build the application. Each HTTP request gets its own connection."""
    api = FastAPI(title="Governed service request runner")

    def store() -> Store:
        opened = Store(database)
        try:
            yield opened
        finally:
            opened.close()

    @api.exception_handler(UnknownReferenceError)
    def unknown(request: Request, error: UnknownReferenceError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"error": str(error)})

    @api.exception_handler(ServiceRequestError)
    def refused(request: Request, error: ServiceRequestError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"error": str(error)})

    @api.get("/health")
    def health() -> HealthOut:
        return HealthOut(status="ok")

    @api.get("/requests")
    def list_requests(opened: Store = Depends(store)) -> list[RequestOut]:
        return [_request_out(tracked) for tracked in opened.tracked_requests()]

    @api.get("/requests/{reference}")
    def get_request(
        reference: str, opened: Store = Depends(store)
    ) -> RequestDetailOut:
        view = request_view(opened, reference)
        return RequestDetailOut(
            request=_request_out(view.tracked),
            steps=[
                StepOut(
                    position=step.position,
                    operation=step.operation,
                    state=step.state.value,
                )
                for step in view.steps
            ],
            approvals=[_approval_out(approval) for approval in view.approvals],
        )

    @api.get("/requests/{reference}/log")
    def get_log(reference: str, opened: Store = Depends(store)) -> list[LogEntryOut]:
        opened.tracked(reference)
        return [
            LogEntryOut(
                position=recorded.position,
                kind=recorded.entry.kind,
                reference=recorded.entry.reference,
                details=dict(recorded.entry.details),
            )
            for recorded in opened.entries(reference)
        ]

    @api.get("/approvals")
    def list_approvals(opened: Store = Depends(store)) -> list[ApprovalOut]:
        return [_approval_out(approval) for approval in opened.pending_approvals()]

    @api.post("/approvals/{reference}")
    def resolve_approval(
        reference: str, body: DecisionBody, opened: Store = Depends(store)
    ) -> RequestOut:
        decide(opened, body.to_domain(reference))
        return _request_out(opened.tracked(reference))

    @api.get("/operations")
    def list_operations() -> list[OperationOut]:
        return [
            OperationOut(name=name, materiality=operation.materiality)
            for name, operation in OPERATIONS.items()
        ]

    @api.get("/policy")
    def list_policy() -> list[PolicyRuleOut]:
        return [
            PolicyRuleOut(
                order=order,
                describe=rule.describe,
                required_role=rule.required_role,
            )
            for order, rule in enumerate(POLICY_RULES, start=1)
        ]

    return api


def _request_out(tracked: TrackedRequest) -> RequestOut:
    request = tracked.request
    return RequestOut(
        reference=request.reference,
        kind=request.kind.value,
        state=tracked.state,
        account=request.account,
        amount=str(request.amount),
    )


def _approval_out(approval: Approval) -> ApprovalOut:
    return ApprovalOut(
        reference=approval.reference,
        position=approval.position,
        role=approval.role,
        state=approval.state.value,
    )
