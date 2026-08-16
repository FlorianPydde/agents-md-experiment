"""Turning a request into its ordered steps."""

from __future__ import annotations

from .models import PLANS, Request, Step


def build(request: Request) -> list[Step]:
    """The ordered steps for a request, with the arguments each one needs."""
    steps: list[Step] = []
    for index, operation in enumerate(PLANS[request.kind]):
        arguments: dict[str, object] = {"account": request.account}
        if operation in ("apply_credit", "apply_debit"):
            arguments["amount"] = request.amount
        if operation == "notify_customer":
            arguments["reference"] = request.reference
            arguments["origin"] = str(request.requester.origin)
        steps.append(Step(index=index, operation=operation, arguments=arguments))
    return steps
