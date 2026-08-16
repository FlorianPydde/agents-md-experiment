"""Policy: whether a step runs on its own or needs approval, and by which role.

The rules are held as an ordered list and the first match wins, so the ordering
in `RULES` is the ordering in the specification.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Callable

from . import operations
from .models import Materiality, Role

#: The threshold above which a credit needs approval.
CREDIT_THRESHOLD = Decimal("100.00")


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    description: str
    role: Role | None
    matches: Callable[[str, dict[str, Any]], bool]


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """What policy said about one step."""

    rule: str
    needs_approval: bool
    role: Role | None

    @property
    def outcome(self) -> str:
        return "needs_approval" if self.needs_approval else "auto"


def _amount(arguments: dict[str, Any]) -> Decimal:
    value = arguments.get("amount")
    return value if isinstance(value, Decimal) else Decimal(str(value or "0"))


def _is_read(operation: str, arguments: dict[str, Any]) -> bool:
    return operations.get(operation).materiality is Materiality.READ


RULES: tuple[Rule, ...] = (
    Rule(
        name="read_runs_alone",
        description="A read operation runs on its own.",
        role=None,
        matches=_is_read,
    ),
    Rule(
        name="small_credit_runs_alone",
        description="apply_credit of 100.00 or less runs on its own.",
        role=None,
        matches=lambda op, args: op == "apply_credit" and _amount(args) <= CREDIT_THRESHOLD,
    ),
    Rule(
        name="large_credit_needs_finance",
        description="apply_credit above 100.00 needs finance.",
        role=Role.FINANCE,
        matches=lambda op, args: op == "apply_credit" and _amount(args) > CREDIT_THRESHOLD,
    ),
    Rule(
        name="debit_needs_finance",
        description="apply_debit needs finance.",
        role=Role.FINANCE,
        matches=lambda op, args: op == "apply_debit",
    ),
    Rule(
        name="freezing_needs_risk",
        description="freeze_account and unfreeze_account need risk.",
        role=Role.RISK,
        matches=lambda op, args: op in ("freeze_account", "unfreeze_account"),
    ),
    Rule(
        name="default_runs_alone",
        description="Anything else runs on its own.",
        role=None,
        matches=lambda op, args: True,
    ),
)


def evaluate(operation: str, arguments: dict[str, Any]) -> PolicyDecision:
    """Apply the rules in order and report the first match."""
    for rule in RULES:
        if rule.matches(operation, arguments):
            return PolicyDecision(
                rule=rule.name, needs_approval=rule.role is not None, role=rule.role
            )
    raise AssertionError("the last rule matches everything")


def catalogue() -> list[dict[str, str | None]]:
    """The rules in order, for reports and the HTTP API."""
    return [
        {
            "position": str(position),
            "rule": rule.name,
            "description": rule.description,
            "role": str(rule.role) if rule.role else None,
        }
        for position, rule in enumerate(RULES, start=1)
    ]
