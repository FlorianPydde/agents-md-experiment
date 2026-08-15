"""Policy: decides whether a step runs on its own or needs a named role.

Rules are evaluated in order and the first match wins. Rules are data, so the
set can grow without changing the engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Sequence

from .models import Materiality, Role
from .operations import Operation

CREDIT_AUTO_LIMIT = Decimal("100.00")


@dataclass(frozen=True)
class Verdict:
    """The outcome of evaluating policy for one step."""

    rule: str
    required_role: Role | None

    @property
    def autonomous(self) -> bool:
        return self.required_role is None

    def to_json(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "autonomous": self.autonomous,
            "required_role": self.required_role.value if self.required_role else None,
        }


@dataclass(frozen=True)
class Rule:
    name: str
    description: str
    required_role: Role | None
    matches: Callable[[Operation, Decimal], bool]

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "required_role": self.required_role.value if self.required_role else None,
        }


RULES: Sequence[Rule] = (
    Rule(
        "read_is_autonomous",
        "A read operation runs on its own.",
        None,
        lambda operation, amount: operation.materiality is Materiality.READ,
    ),
    Rule(
        "small_credit_is_autonomous",
        "apply_credit of 100.00 or less runs on its own.",
        None,
        lambda operation, amount: operation.name == "apply_credit" and amount <= CREDIT_AUTO_LIMIT,
    ),
    Rule(
        "large_credit_needs_finance",
        "apply_credit above 100.00 needs finance.",
        Role.FINANCE,
        lambda operation, amount: operation.name == "apply_credit",
    ),
    Rule(
        "debit_needs_finance",
        "apply_debit needs finance.",
        Role.FINANCE,
        lambda operation, amount: operation.name == "apply_debit",
    ),
    Rule(
        "freeze_state_needs_risk",
        "freeze_account and unfreeze_account need risk.",
        Role.RISK,
        lambda operation, amount: operation.name in ("freeze_account", "unfreeze_account"),
    ),
    Rule(
        "default_autonomous",
        "Anything else runs on its own.",
        None,
        lambda operation, amount: True,
    ),
)


def evaluate(operation: Operation, amount: Decimal) -> Verdict:
    for rule in RULES:
        if rule.matches(operation, amount):
            return Verdict(rule=rule.name, required_role=rule.required_role)
    raise AssertionError("policy rules must be exhaustive")


def rules_json() -> list[dict[str, object]]:
    return [rule.to_json() for rule in RULES]
