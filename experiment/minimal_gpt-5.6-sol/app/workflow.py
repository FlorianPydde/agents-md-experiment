from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .models import (
    AppError,
    Decision,
    Materiality,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    Role,
    ServiceRequest,
    Step,
    StepState,
)
from .storage import Repository, money


PLANS: dict[RequestKind, tuple[OperationName, ...]] = {
    RequestKind.GOODWILL_CREDIT: (
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.ACCOUNT_RECOVERY: (
        OperationName.READ_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
        OperationName.APPLY_CREDIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
    RequestKind.COLLECT_DEBT: (
        OperationName.READ_ACCOUNT,
        OperationName.APPLY_DEBIT,
        OperationName.NOTIFY_CUSTOMER,
    ),
}


@dataclass(frozen=True)
class PolicyResult:
    autonomous: bool
    rule: str
    required_role: Role | None = None


@dataclass(frozen=True)
class Operation:
    name: OperationName
    materiality: Materiality
    execute: Callable[[Repository, ServiceRequest], dict[str, object]]


def evaluate_policy(
    operation: OperationName, materiality: Materiality, amount: Decimal
) -> PolicyResult:
    if materiality is Materiality.READ:
        return PolicyResult(True, "read operations run autonomously")
    if operation is OperationName.APPLY_CREDIT and amount <= Decimal("100.00"):
        return PolicyResult(True, "credits up to 100.00 run autonomously")
    if operation is OperationName.APPLY_CREDIT:
        return PolicyResult(False, "credits above 100.00 require finance", Role.FINANCE)
    if operation is OperationName.APPLY_DEBIT:
        return PolicyResult(False, "debits require finance", Role.FINANCE)
    if operation in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT):
        return PolicyResult(False, "freeze changes require risk", Role.RISK)
    return PolicyResult(True, "other operations run autonomously")


def _account(repository: Repository, request: ServiceRequest):
    account = repository.get_account(request.account_id)
    if account is None:
        raise AppError(f"account not found: {request.account_id}")
    return account


def _require_writable(repository: Repository, request: ServiceRequest) -> None:
    if _account(repository, request).frozen:
        raise AppError(f"account is frozen: {request.account_id}")


def read_account(
    repository: Repository, request: ServiceRequest
) -> dict[str, object]:
    account = _account(repository, request)
    return {
        "account": account.id,
        "balance": money(account.balance),
        "tier": account.tier.value,
        "frozen": account.frozen,
    }


def apply_credit(
    repository: Repository, request: ServiceRequest
) -> dict[str, object]:
    _require_writable(repository, request)
    account = _account(repository, request)
    balance = account.balance + request.amount
    repository.set_balance(account.id, balance)
    return {"account": account.id, "balance": money(balance)}


def apply_debit(
    repository: Repository, request: ServiceRequest
) -> dict[str, object]:
    _require_writable(repository, request)
    account = _account(repository, request)
    if account.balance < request.amount:
        raise AppError(
            f"insufficient balance for {request.account_id}: "
            f"{money(account.balance)} < {money(request.amount)}"
        )
    balance = account.balance - request.amount
    repository.set_balance(account.id, balance)
    return {"account": account.id, "balance": money(balance)}


def freeze_account(
    repository: Repository, request: ServiceRequest
) -> dict[str, object]:
    _require_writable(repository, request)
    repository.set_frozen(request.account_id, True)
    return {"account": request.account_id, "frozen": True}


def unfreeze_account(
    repository: Repository, request: ServiceRequest
) -> dict[str, object]:
    _account(repository, request)
    repository.set_frozen(request.account_id, False)
    return {"account": request.account_id, "frozen": False}


INTERNAL_MESSAGE = "Your service request has been completed by our team."
EXTERNAL_MESSAGE = "Your submitted service request has been completed."


def notify_customer(
    repository: Repository, request: ServiceRequest
) -> dict[str, object]:
    _require_writable(repository, request)
    message = (
        INTERNAL_MESSAGE
        if request.requester.origin is Origin.INTERNAL
        else EXTERNAL_MESSAGE
    )
    repository.add_notification(request.reference, message)
    return {"recipient": request.requester.name, "message": message}


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(
        OperationName.READ_ACCOUNT, Materiality.READ, read_account
    ),
    OperationName.APPLY_CREDIT: Operation(
        OperationName.APPLY_CREDIT, Materiality.WRITE, apply_credit
    ),
    OperationName.APPLY_DEBIT: Operation(
        OperationName.APPLY_DEBIT, Materiality.WRITE, apply_debit
    ),
    OperationName.FREEZE_ACCOUNT: Operation(
        OperationName.FREEZE_ACCOUNT, Materiality.WRITE, freeze_account
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        OperationName.UNFREEZE_ACCOUNT, Materiality.WRITE, unfreeze_account
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        OperationName.NOTIFY_CUSTOMER, Materiality.WRITE, notify_customer
    ),
}


POLICY_RULES = (
    "read operations run autonomously",
    "apply_credit up to 100.00 runs autonomously",
    "apply_credit above 100.00 requires finance",
    "apply_debit requires finance",
    "freeze_account and unfreeze_account require risk",
    "all other operations run autonomously",
)


class Runner:
    def __init__(self, repository: Repository):
        self.repository = repository

    def intake(self, request: ServiceRequest) -> None:
        operations = PLANS[request.kind]
        self.repository.add_request(request, operations)
        self.repository.append_event(
            request.reference,
            "request_received",
            {
                "kind": request.kind.value,
                "account": request.account_id,
                "amount": money(request.amount),
            },
        )
        self.repository.append_event(
            request.reference,
            "plan_created",
            {"steps": [operation.value for operation in operations]},
        )
        self._advance(request.reference)

    def decide(self, reference: str, role: Role, decision: Decision) -> None:
        stored = self.repository.get_request(reference)
        if stored is None:
            raise AppError(f"request not found: {reference}")
        approval = self.repository.pending_approval(reference)
        if approval is None:
            raise AppError(f"request has no pending approval: {reference}")
        if role is not approval.required_role:
            raise AppError(
                f"approval for {reference} requires role "
                f"{approval.required_role.value}, not {role.value}"
            )
        self.repository.resolve_approval(approval.id, role, decision)
        self.repository.append_event(
            reference,
            "approval_resolved",
            {
                "step_id": approval.step_id,
                "role": role.value,
                "decision": decision.value,
            },
        )
        if decision is Decision.REJECT:
            self.repository.set_step_state(approval.step_id, StepState.REJECTED)
            self._finish(reference, RequestState.REJECTED)
            return
        step = next(
            step
            for step in self.repository.list_steps(reference)
            if step.id == approval.step_id
        )
        self._execute(stored.request, step)
        refreshed = self.repository.get_request(reference)
        if refreshed is not None and refreshed.state is not RequestState.FAILED:
            self._advance(reference)

    def _advance(self, reference: str) -> None:
        stored = self.repository.get_request(reference)
        if stored is None:
            raise AppError(f"request not found: {reference}")
        self.repository.set_request_state(reference, RequestState.RECEIVED)
        while (step := self.repository.next_step(reference)) is not None:
            operation = OPERATIONS[step.operation]
            result = evaluate_policy(
                step.operation, operation.materiality, stored.request.amount
            )
            self.repository.append_event(
                reference,
                "policy_decided",
                {
                    "step_id": step.id,
                    "operation": step.operation.value,
                    "autonomous": result.autonomous,
                    "rule": result.rule,
                    "required_role": (
                        result.required_role.value
                        if result.required_role is not None
                        else None
                    ),
                },
            )
            if not result.autonomous:
                assert result.required_role is not None
                self.repository.add_approval(reference, step.id, result.required_role)
                self.repository.set_step_state(step.id, StepState.AWAITING_APPROVAL)
                self.repository.set_request_state(
                    reference, RequestState.AWAITING_APPROVAL
                )
                self.repository.append_event(
                    reference,
                    "approval_requested",
                    {
                        "step_id": step.id,
                        "operation": step.operation.value,
                        "required_role": result.required_role.value,
                    },
                )
                return
            if not self._execute(stored.request, step):
                return
        self._finish(reference, RequestState.COMPLETED)

    def _execute(self, request: ServiceRequest, step: Step) -> bool:
        operation = OPERATIONS[step.operation]
        self.repository.append_event(
            request.reference,
            "operation_started",
            {"step_id": step.id, "operation": step.operation.value},
        )
        try:
            result = operation.execute(self.repository, request)
        except AppError as error:
            self.repository.set_step_state(step.id, StepState.FAILED)
            self.repository.append_event(
                request.reference,
                "operation_failed",
                {
                    "step_id": step.id,
                    "operation": step.operation.value,
                    "error": str(error),
                },
            )
            self._finish(request.reference, RequestState.FAILED)
            return False
        self.repository.set_step_state(step.id, StepState.COMPLETED)
        self.repository.append_event(
            request.reference,
            "operation_completed",
            {
                "step_id": step.id,
                "operation": step.operation.value,
                "result": result,
            },
        )
        return True

    def _finish(self, reference: str, state: RequestState) -> None:
        self.repository.set_request_state(reference, state)
        self.repository.append_event(
            reference, "request_finalized", {"state": state.value}
        )
