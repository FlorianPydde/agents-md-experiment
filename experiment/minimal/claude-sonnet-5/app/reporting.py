"""Formatting helpers for command output.

Kept separate from the engine so that the exact text produced by `run` and
`show` lives in one place and is easy to keep byte-for-byte stable.
"""

from __future__ import annotations

from decimal import Decimal

from app.engine import Engine


def format_request_line(reference: str, kind: str, state: str) -> str:
    return f"{reference:<10}{kind:<20}{state}"


def format_account_line(account_id: str, balance: Decimal, frozen: bool) -> str:
    status = "frozen" if frozen else "active"
    return f"{account_id:<10}{balance:>10.2f}  {status}"


def run_summary_lines(engine: Engine) -> list[str]:
    lines = []
    for row in engine.list_requests():
        lines.append(format_request_line(row["reference"], row["kind"], row["state"]))
    for account in engine.list_accounts():
        lines.append(format_account_line(account.id, account.balance, account.frozen))
    return lines
