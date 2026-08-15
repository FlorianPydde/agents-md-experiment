"""Every closed set in the system."""

from enum import StrEnum


class Tier(StrEnum):
    STANDARD = "standard"
    PREMIUM = "premium"


class Origin(StrEnum):
    INTERNAL = "internal"
    EXTERNAL = "external"


class RequestKind(StrEnum):
    GOODWILL_CREDIT = "goodwill_credit"
    ACCOUNT_RECOVERY = "account_recovery"
    COLLECT_DEBT = "collect_debt"


class RequestState(StrEnum):
    RECEIVED = "received"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class StepState(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"


class OperationName(StrEnum):
    READ_ACCOUNT = "read_account"
    APPLY_CREDIT = "apply_credit"
    APPLY_DEBIT = "apply_debit"
    FREEZE_ACCOUNT = "freeze_account"
    UNFREEZE_ACCOUNT = "unfreeze_account"
    NOTIFY_CUSTOMER = "notify_customer"


class Materiality(StrEnum):
    READ = "read"
    WRITE = "write"


class Role(StrEnum):
    FINANCE = "finance"
    RISK = "risk"
    SUPERVISOR = "supervisor"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ScenarioAction(StrEnum):
    INTAKE = "intake"
    DECIDE = "decide"


class EventKind(StrEnum):
    REQUEST_RECEIVED = "request_received"
    PLAN_CREATED = "plan_created"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OPERATION_RAN = "operation_ran"
    OPERATION_FAILED = "operation_failed"
    REQUEST_FINALISED = "request_finalised"


class ExportFormat(StrEnum):
    CSV = "csv"
    JSON = "json"
    TSV = "tsv"
