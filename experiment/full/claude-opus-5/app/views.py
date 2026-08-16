"""Reading the store for display and for export."""

from dataclasses import dataclass

from app.domain import Account, Approval, StepRecord, TrackedRequest
from app.events import RecordedEntry
from app.store import Store


@dataclass(frozen=True)
class RequestView:
    """Everything known about one request."""

    tracked: TrackedRequest
    steps: tuple[StepRecord, ...]
    approvals: tuple[Approval, ...]
    entries: tuple[RecordedEntry, ...]


def request_view(store: Store, reference: str) -> RequestView:
    return RequestView(
        tracked=store.tracked(reference),
        steps=store.step_records(reference),
        approvals=store.approvals(reference),
        entries=store.entries(reference),
    )


def request_line(tracked: TrackedRequest) -> str:
    request = tracked.request
    return f"{request.reference:<10}{request.kind.value:<20}{tracked.state.value}"


def account_line(account: Account) -> str:
    return f"{account.id:<10}{account.balance.amount:>10.2f}  {account.state_word()}"


def summary(store: Store) -> str:
    lines = [request_line(tracked) for tracked in store.tracked_requests()]
    lines.extend(account_line(account) for account in store.accounts())
    return "\n".join(lines)


def render_request(view: RequestView) -> str:
    request = view.tracked.request
    lines = [
        f"{request.reference}  {request.kind.value}  {view.tracked.state.value}",
        f"  account   {request.account}",
        f"  amount    {request.amount}",
        f"  requester {request.requester.name} "
        f"({request.requester.role}, {request.requester.origin.value})",
        "  steps",
    ]
    lines.extend(
        f"    {step.position}  {step.operation.value:<20}{step.state.value}"
        for step in view.steps
    )
    lines.append("  approvals")
    lines.extend(
        f"    step {approval.position}  {approval.role.value:<12}{approval.state.value}"
        for approval in view.approvals
    )
    if not view.approvals:
        lines.append("    none")
    lines.append("  log")
    lines.extend(
        f"    {recorded.entry.kind.value:<20}{recorded.entry.rendered_details()}"
        for recorded in view.entries
    )
    return "\n".join(lines)
