"""Static domain knowledge: request kinds, plans, operations, and policy rules.

None of this module touches the database or the filesystem. It is pure data plus
pure functions, which makes it easy to unit test in isolation.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.errors import AppError

REQUEST_KINDS = ("goodwill_credit", "account_recovery", "collect_debt")

ACCOUNT_TIERS = ("standard", "premium")

REQUESTER_ORIGINS = ("internal", "external")

DECISIONS = ("approve", "reject")

APPROVAL_ROLES = ("finance", "risk", "supervisor")

REQUEST_STATES = (
    "received",
    "awaiting_approval",
    "completed",
    "rejected",
    "failed",
)

STEP_STATES = ("pending", "running", "done", "failed", "skipped")

OPERATIONS = (
    "read_account",
    "apply_credit",
    "apply_debit",
    "freeze_account",
    "unfreeze_account",
    "notify_customer",
)

MATERIALITY = {
    "read_account": "read",
    "apply_credit": "write",
    "apply_debit": "write",
    "freeze_account": "write",
    "unfreeze_account": "write",
    "notify_customer": "write",
}

PLANS = {
    "goodwill_credit": ("read_account", "apply_credit", "notify_customer"),
    "account_recovery": (
        "read_account",
        "unfreeze_account",
        "apply_credit",
        "notify_customer",
    ),
    "collect_debt": ("read_account", "apply_debit", "notify_customer"),
}

NOTIFY_TEMPLATES = {
    "internal": "Internal notice: request {reference} for account {account} has been processed.",
    "external": "Dear {owner}, your request {reference} has been processed.",
}

GOODWILL_AUTO_LIMIT = Decimal("100.00")


@dataclass(frozen=True)
class PolicyRule:
    """One row of the policy table, in the order it is checked."""

    number: int
    description: str


POLICY_RULES: tuple[PolicyRule, ...] = (
    PolicyRule(1, "A read operation runs on its own."),
    PolicyRule(2, "apply_credit of 100.00 or less runs on its own."),
    PolicyRule(3, "apply_credit above 100.00 needs finance."),
    PolicyRule(4, "apply_debit needs finance."),
    PolicyRule(5, "freeze_account and unfreeze_account need risk."),
    PolicyRule(6, "Anything else runs on its own."),
)


@dataclass(frozen=True)
class PolicyDecision:
    """Result of evaluating policy for a single step."""

    needs_approval: bool
    required_role: str | None
    rule_number: int


def evaluate_policy(operation: str, args: dict) -> PolicyDecision:
    """Decide whether an operation may run on its own or needs approval.

    The first matching rule wins, in the order listed in the spec.
    """
    materiality = MATERIALITY.get(operation)
    if materiality == "read":
        return PolicyDecision(needs_approval=False, required_role=None, rule_number=1)

    if operation == "apply_credit":
        amount = _decimal_arg(args, "amount")
        if amount <= GOODWILL_AUTO_LIMIT:
            return PolicyDecision(needs_approval=False, required_role=None, rule_number=2)
        return PolicyDecision(needs_approval=True, required_role="finance", rule_number=3)

    if operation == "apply_debit":
        return PolicyDecision(needs_approval=True, required_role="finance", rule_number=4)

    if operation in ("freeze_account", "unfreeze_account"):
        return PolicyDecision(needs_approval=True, required_role="risk", rule_number=5)

    return PolicyDecision(needs_approval=False, required_role=None, rule_number=6)


def _decimal_arg(args: dict, key: str) -> Decimal:
    value = args.get(key)
    if value is None:
        raise AppError(f"operation argument '{key}' is missing")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise AppError(f"operation argument '{key}' is not a valid decimal: {value!r}") from exc


def plan_for(kind: str) -> tuple[str, ...]:
    if kind not in PLANS:
        raise AppError(f"unknown request kind: {kind!r}")
    return PLANS[kind]
