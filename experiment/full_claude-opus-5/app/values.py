"""Value objects: primitives that carry a unit or a rule."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


@dataclass(frozen=True, order=True)
class Money:
    """A non negative decimal amount of account currency."""

    amount: Decimal

    def __post_init__(self) -> None:
        if self.amount < 0:
            raise ValueError("Money cannot be negative")

    @classmethod
    def parse(cls, text: str) -> Money:
        try:
            return cls(Decimal(text))
        except (InvalidOperation, ArithmeticError) as exc:
            raise ValueError(f"not a decimal amount: {text!r}") from exc

    def plus(self, other: Money) -> Money:
        return Money(self.amount + other.amount)

    def covers(self, other: Money) -> bool:
        return self.amount >= other.amount

    def minus(self, other: Money) -> Money:
        if not self.covers(other):
            raise ValueError("Money cannot be negative")
        return Money(self.amount - other.amount)

    def exceeds(self, other: Money) -> bool:
        return self.amount > other.amount

    def formatted(self) -> str:
        return f"{self.amount:.2f}"

    def __str__(self) -> str:
        return self.formatted()
