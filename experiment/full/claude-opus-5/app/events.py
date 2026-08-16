"""Log entries. Written once, never edited, never deleted."""

from dataclasses import dataclass

from app.enums import EventKind


@dataclass(frozen=True)
class LogEntry:
    """What happened, which request it belongs to, and ordered data about it."""

    kind: EventKind
    reference: str
    details: tuple[tuple[str, str], ...] = ()

    def rendered_details(self) -> str:
        return ", ".join(f"{name}={value}" for name, value in self.details)


@dataclass(frozen=True)
class RecordedEntry:
    """A log entry as it came back from the log, with its position."""

    position: int
    entry: LogEntry
