"""Report generation: the `run` summary text, and export records."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain import RequestKind, RequestState
from app.store import Store


def format_run_summary(store: Store) -> str:
    lines: list[str] = []
    for req in store.all_requests_in_order():
        lines.append(f"{req.reference:<10}{req.kind.value:<20}{req.state.value}")
    for account in store.all_accounts():
        status = "frozen" if account.frozen else "active"
        lines.append(f"{account.id:<10}{account.balance:>10.2f}  {status}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ExportRecord:
    reference: str
    kind: RequestKind
    state: RequestState
    account: str
    amount: Decimal


def export_records(store: Store) -> list[ExportRecord]:
    return [
        ExportRecord(
            reference=req.reference,
            kind=req.kind,
            state=req.state,
            account=req.account,
            amount=req.amount,
        )
        for req in store.all_requests_in_order()
    ]
