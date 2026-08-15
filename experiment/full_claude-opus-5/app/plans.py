"""Plans: the ordered steps a request kind expands into."""

from __future__ import annotations

from .enums import OperationName, RequestKind

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

if _missing := set(RequestKind) - PLANS.keys():
    raise RuntimeError(f"No plan registered for: {_missing}")
