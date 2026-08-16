"""Rendering: the run summary and the detail a caller asks for by reference."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

from .domain import Account, Approval, Event, RequestRecord
from .errors import UsageError
from .money import format_amount
from .store import Store

REFERENCE_WIDTH = 10
KIND_WIDTH = 20
ACCOUNT_WIDTH = 10
BALANCE_WIDTH = 10


def request_line(record: RequestRecord) -> str:
    return (
        f"{record.request.reference:<{REFERENCE_WIDTH}}"
        f"{record.request.kind.value:<{KIND_WIDTH}}"
        f"{record.state.value}"
    )


def account_line(account: Account) -> str:
    status = "frozen" if account.frozen else "active"
    return (
        f"{account.id:<{ACCOUNT_WIDTH}}"
        f"{format_amount(account.balance):>{BALANCE_WIDTH}}  {status}"
    )


def summary_lines(records: Iterable[RequestRecord], accounts: Iterable[Account]) -> list[str]:
    """One line per request in arrival order, then one line per account by id."""
    lines = [request_line(record) for record in records]
    lines.extend(account_line(account) for account in accounts)
    return lines


@dataclass(frozen=True, slots=True)
class RequestDetail:
    """Everything known about one request."""

    record: RequestRecord
    approvals: tuple[Approval, ...]
    events: tuple[Event, ...]


class DetailReader:
    """Looks a reference up once, however many times it is asked for."""

    def __init__(self, store: Store) -> None:
        self._store = store
        self._load = lru_cache(maxsize=None)(self._load_uncached)

    def _load_uncached(self, reference: str) -> RequestDetail:
        record = self._store.record(reference)
        if record is None:
            raise UsageError(f"no request named {reference}")
        return RequestDetail(
            record=record,
            approvals=self._store.approvals_for(reference),
            events=self._store.events_for(reference),
        )

    def detail(self, reference: str) -> RequestDetail:
        return self._load(reference)

    @property
    def lookups(self) -> int:
        return self._load.cache_info().misses


def detail_lines(detail: RequestDetail) -> list[str]:
    """A readable rendering of one request, its steps, approvals and log."""
    request = detail.record.request
    lines = [
        f"request   {request.reference}",
        f"kind      {request.kind.value}",
        f"account   {request.account}",
        f"amount    {format_amount(request.amount)}",
        f"requester {request.requester.name} ({request.requester.role}, "
        f"{request.requester.origin.value})",
        f"state     {detail.record.state.value}",
        "steps",
    ]
    for step in detail.record.steps:
        role = step.required_role.value if step.required_role else "-"
        lines.append(f"  {step.index}  {step.operation.value:<20}{step.state.value:<20}{role}")
    lines.append("approvals")
    if not detail.approvals:
        lines.append("  none")
    for approval in detail.approvals:
        lines.append(
            f"  {approval.step_index}  {approval.operation.value:<20}"
            f"{approval.role.value:<12}{approval.state.value}"
        )
    lines.append("log")
    for event in detail.events:
        rendered = " ".join(f"{key}={value}" for key, value in event.details)
        lines.append(f"  {event.sequence:>4}  {event.type.value:<20}{rendered}")
    return lines
