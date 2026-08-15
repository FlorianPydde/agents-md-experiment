from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from .models import (
    Account,
    ApprovalRole,
    Decision,
    DomainError,
    Materiality,
    Money,
    OperationName,
    Origin,
    RequestKind,
    RequestState,
    ServiceRequest,
    StepState,
)
from .storage import Store


INTERNAL_NOTIFICATION = "Internal service request completed."
EXTERNAL_NOTIFICATION = "Your service request has been processed."


@dataclass(frozen=True)
class Operation:
    materiality: Materiality
    validate: Callable[[ServiceRequest, Account], None]
    execute: Callable[[ServiceRequest, Account], Account]


def _validate_account(_: ServiceRequest, __: Account) -> None:
    return None


def _validate_unfrozen(_: ServiceRequest, account: Account) -> None:
    if account.frozen:
        raise DomainError("account is frozen")


def _validate_debit(request: ServiceRequest, account: Account) -> None:
    _validate_unfrozen(request, account)
    if account.balance.amount < request.amount.amount:
        raise DomainError("insufficient balance")


def _read(_: ServiceRequest, account: Account) -> Account:
    return account


def _credit(request: ServiceRequest, account: Account) -> Account:
    return Account(account.identifier, account.owner, account.tier, Money(account.balance.amount + request.amount.amount), account.frozen)


def _debit(request: ServiceRequest, account: Account) -> Account:
    return Account(account.identifier, account.owner, account.tier, Money(account.balance.amount - request.amount.amount), account.frozen)


def _freeze(_: ServiceRequest, account: Account) -> Account:
    return Account(account.identifier, account.owner, account.tier, account.balance, True)


def _unfreeze(_: ServiceRequest, account: Account) -> Account:
    return Account(account.identifier, account.owner, account.tier, account.balance, False)


def _notify(_: ServiceRequest, account: Account) -> Account:
    return account


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(Materiality.READ, _validate_account, _read),
    OperationName.APPLY_CREDIT: Operation(Materiality.WRITE, _validate_unfrozen, _credit),
    OperationName.APPLY_DEBIT: Operation(Materiality.WRITE, _validate_debit, _debit),
    OperationName.FREEZE_ACCOUNT: Operation(Materiality.WRITE, _validate_unfrozen, _freeze),
    OperationName.UNFREEZE_ACCOUNT: Operation(Materiality.WRITE, _validate_account, _unfreeze),
    OperationName.NOTIFY_CUSTOMER: Operation(Materiality.WRITE, _validate_unfrozen, _notify),
}
if missing := set(OperationName) - OPERATIONS.keys():
    raise RuntimeError(f"unregistered operations: {missing}")

PLANS: dict[RequestKind, tuple[OperationName, ...]] = {
    RequestKind.GOODWILL_CREDIT: (OperationName.READ_ACCOUNT, OperationName.APPLY_CREDIT, OperationName.NOTIFY_CUSTOMER),
    RequestKind.ACCOUNT_RECOVERY: (OperationName.READ_ACCOUNT, OperationName.UNFREEZE_ACCOUNT, OperationName.APPLY_CREDIT, OperationName.NOTIFY_CUSTOMER),
    RequestKind.COLLECT_DEBT: (OperationName.READ_ACCOUNT, OperationName.APPLY_DEBIT, OperationName.NOTIFY_CUSTOMER),
}
if missing := set(RequestKind) - PLANS.keys():
    raise RuntimeError(f"unregistered plans: {missing}")


def required_approval(operation: OperationName, amount: Money) -> ApprovalRole | None:
    if OPERATIONS[operation].materiality is Materiality.READ:
        return None
    if operation is OperationName.APPLY_CREDIT:
        return ApprovalRole.FINANCE if amount.amount > Decimal("100.00") else None
    if operation is OperationName.APPLY_DEBIT:
        return ApprovalRole.FINANCE
    if operation in (OperationName.FREEZE_ACCOUNT, OperationName.UNFREEZE_ACCOUNT):
        return ApprovalRole.RISK
    return None


class Service:
    def __init__(self, store: Store) -> None:
        self.store = store

    def intake(self, request: ServiceRequest) -> None:
        if self.store.pending_approval(request.reference) is not None:
            raise DomainError(f"request already exists: {request.reference}")
        try:
            self.store.get_request(request.reference)
        except LookupError:
            pass
        else:
            raise DomainError(f"request already exists: {request.reference}")
        self.store.get_account(request.account)
        plan = PLANS[request.kind]
        self.store.add_request(request, tuple(operation.value for operation in plan))
        self.store.log(request.reference, "request_received", kind=request.kind.value, account=request.account)
        self.store.log(request.reference, "plan_created", steps=[operation.value for operation in plan])
        self._continue(request.reference)

    def decide(self, reference: str, role: ApprovalRole, decision: Decision) -> None:
        approval = self.store.pending_approval(reference)
        if approval is None:
            raise DomainError(f"request has no pending approval: {reference}")
        required = ApprovalRole(approval["required_role"])
        if role is not required:
            raise DomainError(f"approval for {reference} requires role {required.value}")
        self.store.resolve_approval(reference, approval["step_position"], decision.value)
        self.store.log(reference, "approval_resolved", role=role.value, decision=decision.value, step=approval["step_position"])
        request, _, next_step = self.store.get_request(reference)
        if decision is Decision.REJECT:
            self.store.set_step_state(reference, approval["step_position"], StepState.REJECTED)
            self.store.set_request_progress(reference, RequestState.REJECTED, next_step)
            self.store.log(reference, "request_finalized", state=RequestState.REJECTED.value)
            return
        self._run_step(request, approval["step_position"])
        _, state, _ = self.store.get_request(reference)
        if state is not RequestState.FAILED:
            self._continue(reference)

    def _continue(self, reference: str) -> None:
        request, state, next_step = self.store.get_request(reference)
        if state in (RequestState.FAILED, RequestState.REJECTED, RequestState.COMPLETED):
            return
        steps = self.store.steps(reference)
        while next_step < len(steps):
            operation = OperationName(steps[next_step]["operation"])
            approval = required_approval(operation, request.amount)
            self.store.log(
                reference, "policy_decided", step=next_step, operation=operation.value,
                required_role=None if approval is None else approval.value,
            )
            if approval is not None:
                self.store.request_approval(reference, next_step, approval)
                self.store.set_request_progress(reference, RequestState.AWAITING_APPROVAL, next_step)
                self.store.log(reference, "approval_requested", step=next_step, role=approval.value)
                return
            self._run_step(request, next_step)
            _, state, next_step = self.store.get_request(reference)
            if state is RequestState.FAILED:
                return
        self.store.set_request_progress(reference, RequestState.COMPLETED, next_step)
        self.store.log(reference, "request_finalized", state=RequestState.COMPLETED.value)

    def _run_step(self, request: ServiceRequest, position: int) -> None:
        operation_name = OperationName(self.store.steps(request.reference)[position]["operation"])
        account = self.store.get_account(request.account)
        operation = OPERATIONS[operation_name]
        try:
            operation.validate(request, account)
            updated = operation.execute(request, account)
        except DomainError as error:
            self.store.set_step_state(request.reference, position, StepState.FAILED)
            self.store.set_request_progress(request.reference, RequestState.FAILED, position)
            self.store.log(request.reference, "operation_failed", step=position, operation=operation_name.value, reason=str(error))
            self.store.log(request.reference, "request_finalized", state=RequestState.FAILED.value)
            return
        self.store.save_account(updated)
        data: dict[str, object] = {"step": position, "operation": operation_name.value}
        if operation_name is OperationName.NOTIFY_CUSTOMER:
            data["message"] = INTERNAL_NOTIFICATION if request.requester.origin is Origin.INTERNAL else EXTERNAL_NOTIFICATION
        if operation_name is OperationName.READ_ACCOUNT:
            data["balance"] = account.balance.text()
            data["tier"] = account.tier.value
            data["frozen"] = account.frozen
        self.store.set_step_state(request.reference, position, StepState.COMPLETED)
        self.store.set_request_progress(request.reference, RequestState.RECEIVED, position + 1)
        self.store.log(request.reference, "operation_ran", **data)

    def request_view(self, reference: str) -> dict[str, object]:
        request, state, _ = self.store.get_request(reference)
        return {
            "reference": request.reference, "kind": request.kind.value, "state": state.value,
            "account": request.account, "amount": request.amount.text(),
            "steps": [{"position": row["position"], "operation": row["operation"], "state": row["state"]} for row in self.store.steps(reference)],
            "approvals": [
                {"step": row["step_position"], "required_role": row["required_role"], "decision": row["decision"]}
                for row in self.store.approvals(reference)
            ],
        }

    def list_requests(self) -> list[dict[str, str]]:
        return [
            {"reference": request.reference, "kind": request.kind.value, "state": state.value, "account": request.account, "amount": request.amount.text()}
            for request, state, _ in self.store.requests()
        ]
