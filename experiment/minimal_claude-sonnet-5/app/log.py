"""Append-only log event kinds and record shape.

Log entries are never edited or deleted. Each records what happened, the
request it belongs to (if any), and ordered data about the occurrence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class LogEventKind(StrEnum):
    REQUEST_RECEIVED = "request_received"
    PLAN_CREATED = "plan_created"
    POLICY_DECIDED = "policy_decided"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    OPERATION_RAN = "operation_ran"
    OPERATION_FAILED = "operation_failed"
    REQUEST_STATE_CHANGED = "request_state_changed"


@dataclass(frozen=True)
class LogEntry:
    seq: int
    kind: LogEventKind
    reference: str | None
    data: dict[str, Any]
    """Ordered data about the occurrence (insertion order preserved on read)."""
