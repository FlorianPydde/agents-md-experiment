"""Turning stored state into the text the commands print.

No timestamp ever appears here, so repeated runs produce identical text.
"""

from __future__ import annotations

import json
from functools import lru_cache

from .errors import AppError
from .store import Store


def summary(store: Store) -> str:
    """One line per request in arrival order, then one line per account by id."""
    lines: list[str] = []
    for request, state in store.requests():
        lines.append(f"{request.reference:<10}{str(request.kind):<20}{state}")
    for account in store.accounts():
        mark = "frozen" if account.frozen else "active"
        lines.append(f"{account.id:<10}{account.balance:>10.2f}  {mark}")
    return "\n".join(lines)


class Lookup:
    """Reads one request and everything attached to it.

    The reads are cached, so asking for the same reference twice does the
    underlying work once.
    """

    def __init__(self, store: Store) -> None:
        self.store = store
        self._fetch = lru_cache(maxsize=None)(self._load)

    def _load(self, reference: str) -> dict[str, object]:
        record = self.store.request(reference)
        if record is None:
            raise AppError(f"no such request '{reference}'")
        request, state = record
        return {
            "reference": request.reference,
            "kind": str(request.kind),
            "account": request.account,
            "amount": f"{request.amount:.2f}",
            "requester": {
                "name": request.requester.name,
                "role": request.requester.role,
                "origin": str(request.requester.origin),
            },
            "state": str(state),
            "steps": self.store.steps(reference),
            "approvals": [
                {
                    "step": approval.step_index,
                    "operation": approval.operation,
                    "role": str(approval.role),
                    "state": approval.state,
                    "decision": approval.decision,
                }
                for approval in self.store.approvals(reference)
            ],
            "log": self.store.events(reference),
        }

    def fetch(self, reference: str) -> dict[str, object]:
        return self._fetch(reference)

    @property
    def hits(self) -> int:
        """How many lookups were served from the cache."""
        return self._fetch.cache_info().hits


def render(detail: dict[str, object]) -> str:
    """The text `show` prints for one request."""
    requester = detail["requester"]
    lines = [
        f"request    {detail['reference']}",
        f"kind       {detail['kind']}",
        f"account    {detail['account']}",
        f"amount     {detail['amount']}",
        f"requester  {requester['name']} ({requester['role']}, {requester['origin']})",
        f"state      {detail['state']}",
        "",
        "steps",
    ]
    for step in detail["steps"]:
        lines.append(f"  {step['position']}  {step['operation']:<20}{step['state']}")

    lines.extend(["", "approvals"])
    if not detail["approvals"]:
        lines.append("  none")
    for approval in detail["approvals"]:
        decision = approval["decision"] or "-"
        lines.append(
            f"  {approval['step']}  {approval['operation']:<20}"
            f"{approval['role']:<12}{approval['state']:<10}{decision}"
        )

    lines.extend(["", "log"])
    for event in detail["log"]:
        lines.append(f"  {event['type']:<20}{json.dumps(event['data'], sort_keys=False)}")
    return "\n".join(lines)
