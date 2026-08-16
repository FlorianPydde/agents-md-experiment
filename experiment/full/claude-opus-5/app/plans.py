"""Plans. The kind of a request determines its ordered steps."""

from dataclasses import dataclass

from app.domain import ServiceRequest
from app.enums import OperationName, RequestKind, StepState
from app.operations import OPERATIONS, StepArgs


@dataclass(frozen=True)
class Step:
    """One position in a plan, with the arguments it will be called with."""

    position: int
    operation: OperationName
    args: StepArgs
    state: StepState = StepState.PENDING


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

if missing := set(RequestKind) - PLANS.keys():
    raise RuntimeError(f"No plan registered for: {missing}")


def plan_for(request: ServiceRequest) -> tuple[Step, ...]:
    return tuple(
        Step(
            position=position,
            operation=operation,
            args=OPERATIONS[operation].build_args(request),
        )
        for position, operation in enumerate(PLANS[request.kind])
    )
