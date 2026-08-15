from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from .domain import (
    Account,
    ApprovalRole,
    ApprovalState,
    ConflictError,
    Decision,
    EventKind,
    Materiality,
    NotFoundError,
    OperationError,
    OperationName,
    Origin,
    PolicyOutcome,
    RequestKind,
    RequestState,
    ServiceRequest,
    StepState,
)
from .storage import StepRecord, Store


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

if missing_plans := set(RequestKind) - PLANS.keys():
    raise RuntimeError(f"No plan registered for: {missing_plans}")


@dataclass(frozen=True)
class OperationContext:
    account: Account
    request: ServiceRequest


@dataclass(frozen=True)
class OperationResult:
    account: Account
    details: dict[str, str | bool]


ValidateOperation = Callable[[OperationContext], None]
ExecuteOperation = Callable[[OperationContext], OperationResult]


@dataclass(frozen=True)
class Operation:
    materiality: Materiality
    validate: ValidateOperation
    execute: ExecuteOperation


def _validate_account(context: OperationContext) -> None:
    if context.account.account_id != context.request.account_id:
        raise OperationError("operation account does not match the request")


def _validate_writable(context: OperationContext) -> None:
    _validate_account(context)
    if context.account.frozen:
        raise OperationError(f"account {context.account.account_id} is frozen")


def _read_account(context: OperationContext) -> OperationResult:
    account = context.account
    return OperationResult(
        account,
        {
            "balance": account.balance.display(),
            "tier": account.tier.value,
            "frozen": account.frozen,
        },
    )


def _apply_credit(context: OperationContext) -> OperationResult:
    account = context.account.credited(context.request.amount)
    return OperationResult(
        account,
        {
            "amount": context.request.amount.display(),
            "balance": account.balance.display(),
        },
    )


def _apply_debit(context: OperationContext) -> OperationResult:
    account = context.account.debited(context.request.amount)
    return OperationResult(
        account,
        {
            "amount": context.request.amount.display(),
            "balance": account.balance.display(),
        },
    )


def _freeze_account(context: OperationContext) -> OperationResult:
    account = context.account.with_frozen(True)
    return OperationResult(account, {"frozen": account.frozen})


def _unfreeze_account(context: OperationContext) -> OperationResult:
    account = context.account.with_frozen(False)
    return OperationResult(account, {"frozen": account.frozen})


def _notify_customer(context: OperationContext) -> OperationResult:
    if context.request.requester.origin is Origin.INTERNAL:
        message = "Your internal service request has been completed."
    else:
        message = "Your submitted service request has been completed."
    return OperationResult(context.account, {"message": message})


OPERATIONS: dict[OperationName, Operation] = {
    OperationName.READ_ACCOUNT: Operation(
        Materiality.READ, _validate_account, _read_account
    ),
    OperationName.APPLY_CREDIT: Operation(
        Materiality.WRITE, _validate_writable, _apply_credit
    ),
    OperationName.APPLY_DEBIT: Operation(
        Materiality.WRITE, _validate_writable, _apply_debit
    ),
    OperationName.FREEZE_ACCOUNT: Operation(
        Materiality.WRITE, _validate_writable, _freeze_account
    ),
    OperationName.UNFREEZE_ACCOUNT: Operation(
        Materiality.WRITE, _validate_account, _unfreeze_account
    ),
    OperationName.NOTIFY_CUSTOMER: Operation(
        Materiality.WRITE, _validate_writable, _notify_customer
    ),
}

if missing_operations := set(OperationName) - OPERATIONS.keys():
    raise RuntimeError(f"No operation registered for: {missing_operations}")


@dataclass(frozen=True)
class PolicyRuleDescription:
    order: int
    rule: str
    outcome: str


POLICY_RULES: tuple[PolicyRuleDescription, ...] = (
    PolicyRuleDescription(1, "materiality is read", "automatic"),
    PolicyRuleDescription(2, "apply_credit amount <= 100.00", "automatic"),
    PolicyRuleDescription(3, "apply_credit amount > 100.00", "finance"),
    PolicyRuleDescription(4, "operation is apply_debit", "finance"),
    PolicyRuleDescription(
        5, "operation is freeze_account or unfreeze_account", "risk"
    ),
    PolicyRuleDescription(6, "anything else", "automatic"),
)


def decide_policy(
    operation_name: OperationName,
    materiality: Materiality,
    amount: Decimal,
) -> PolicyOutcome:
    if materiality is Materiality.READ:
        return PolicyOutcome(None)
    if operation_name is OperationName.APPLY_CREDIT:
        if amount <= Decimal("100.00"):
            return PolicyOutcome(None)
        return PolicyOutcome(ApprovalRole.FINANCE)
    if operation_name is OperationName.APPLY_DEBIT:
        return PolicyOutcome(ApprovalRole.FINANCE)
    if operation_name in (
        OperationName.FREEZE_ACCOUNT,
        OperationName.UNFREEZE_ACCOUNT,
    ):
        return PolicyOutcome(ApprovalRole.RISK)
    return PolicyOutcome(None)


class Runner:
    def __init__(self, store: Store) -> None:
        self.store = store

    def intake(self, request: ServiceRequest) -> None:
        if self.store.get_request(request.reference) is not None:
            raise ConflictError(f"request {request.reference} already exists")
        if self.store.get_account(request.account_id) is None:
            raise NotFoundError(f"account {request.account_id} does not exist")

        plan = PLANS[request.kind]
        self.store.insert_request(request, plan)
        self.store.append_event(
            request.reference,
            EventKind.REQUEST_RECEIVED,
            {
                "kind": request.kind.value,
                "account": request.account_id,
                "amount": request.amount.display(),
            },
        )
        self.store.append_event(
            request.reference,
            EventKind.PLAN_CREATED,
            {"steps": ",".join(operation.value for operation in plan)},
        )
        self._continue(request.reference)

    def decide(
        self,
        reference: str,
        role: ApprovalRole,
        decision: Decision,
    ) -> None:
        request_record = self.store.get_request(reference)
        if request_record is None:
            raise NotFoundError(f"request {reference} does not exist")
        approval = self.store.get_pending_approval(reference)
        if approval is None:
            raise ConflictError(f"request {reference} has no pending approval")
        if role is not approval.required_role:
            raise ConflictError(
                f"request {reference} requires decision from "
                f"{approval.required_role.value}, not {role.value}"
            )

        approval_state = (
            ApprovalState.APPROVED
            if decision is Decision.APPROVE
            else ApprovalState.REJECTED
        )
        self.store.resolve_approval(approval, approval_state, role)
        self.store.append_event(
            reference,
            EventKind.APPROVAL_RESOLVED,
            {
                "operation": approval.operation.value,
                "role": role.value,
                "decision": decision.value,
            },
        )

        if decision is Decision.REJECT:
            self.store.set_step_state(approval.step_id, StepState.SKIPPED)
            self.store.skip_remaining_steps(reference)
            self._finalize(reference, RequestState.REJECTED)
            return

        self.store.set_step_state(approval.step_id, StepState.PENDING)
        step = self.store.next_pending_step(reference)
        if step is None or step.step_id != approval.step_id:
            raise ConflictError(f"request {reference} approval step is inconsistent")
        if self._run_operation(request_record.request, step):
            self._continue(reference)

    def _continue(self, reference: str) -> None:
        request_record = self.store.get_request(reference)
        if request_record is None:
            raise NotFoundError(f"request {reference} does not exist")

        while (step := self.store.next_pending_step(reference)) is not None:
            operation = OPERATIONS[step.operation]
            outcome = decide_policy(
                step.operation,
                operation.materiality,
                request_record.request.amount.amount,
            )
            self.store.append_event(
                reference,
                EventKind.POLICY_DECIDED,
                {
                    "operation": step.operation.value,
                    "outcome": (
                        "automatic"
                        if outcome.automatic
                        else outcome.required_role.value
                    ),
                },
            )
            if not outcome.automatic:
                required_role = outcome.required_role
                if required_role is None:
                    raise RuntimeError("approval outcome is missing its role")
                self.store.set_step_state(
                    step.step_id, StepState.AWAITING_APPROVAL
                )
                self.store.create_approval(reference, step, required_role)
                self.store.set_request_state(
                    reference, RequestState.AWAITING_APPROVAL
                )
                self.store.append_event(
                    reference,
                    EventKind.APPROVAL_REQUESTED,
                    {
                        "operation": step.operation.value,
                        "role": required_role.value,
                    },
                )
                return
            if not self._run_operation(request_record.request, step):
                return

        self._finalize(reference, RequestState.COMPLETED)

    def _run_operation(
        self, request: ServiceRequest, step: StepRecord
    ) -> bool:
        account = self.store.get_account(request.account_id)
        if account is None:
            raise NotFoundError(f"account {request.account_id} does not exist")
        operation = OPERATIONS[step.operation]
        context = OperationContext(account, request)
        try:
            operation.validate(context)
            result = operation.execute(context)
        except OperationError as error:
            self.store.set_step_state(step.step_id, StepState.FAILED)
            self.store.skip_remaining_steps(request.reference)
            self.store.append_event(
                request.reference,
                EventKind.OPERATION_FAILED,
                {
                    "operation": step.operation.value,
                    "error": str(error),
                },
            )
            self._finalize(request.reference, RequestState.FAILED)
            return False

        if result.account != account:
            self.store.save_account(result.account)
        self.store.set_step_state(step.step_id, StepState.COMPLETED)
        self.store.append_event(
            request.reference,
            EventKind.OPERATION_RAN,
            {"operation": step.operation.value, **result.details},
        )
        return True

    def _finalize(self, reference: str, state: RequestState) -> None:
        self.store.set_request_state(reference, state)
        self.store.append_event(
            reference,
            EventKind.REQUEST_FINALIZED,
            {"state": state.value},
        )
