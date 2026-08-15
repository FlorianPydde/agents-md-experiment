# Governed Service Request Runner

A back office prototype. Service requests arrive from an intake system, each becomes an ordered
plan of steps, and those steps run under policy control.
Some steps run on their own; others stop and wait for a named role to approve them.
Everything that happens is appended to a SQLite log that survives between runs.

## Running it

Python 3.12 with `uv`. From this folder:

```
uv sync
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
```

`--db PATH` overrides the database location (default `governed.db` in this folder).

Tests:

```
uv run pytest
```

## Commands

- `run SCENARIO --world WORLD` starts from a clean database, replays the scenario, and prints a
  summary: one line per request in arrival order, then one line per account sorted by id.
  The output contains no timestamps, so repeated runs produce identical text.
- `show REF [REF ...]` prints a request, its steps, its approvals, and its log entries.
  An unknown reference exits non-zero with a message naming it.
  Asking twice for the same reference in one invocation does the underlying lookup once.
- `export --format {csv,json,tsv}` writes `report.<ext>` with one record per request.
- `serve` runs the HTTP API.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/requests` | list requests |
| GET | `/requests/{reference}` | one request with steps and approvals |
| GET | `/requests/{reference}/log` | log entries for a request |
| GET | `/approvals` | pending approvals |
| POST | `/approvals/{reference}` | resolve with `{"role": ..., "decision": ...}` |
| GET | `/operations` | supported operations and their materiality |
| GET | `/policy` | the numbered policy rules |

## How the pieces fit

```
parsing.py     the edge: raw JSON becomes validated domain records, or an AppError
domain.py      the vocabulary: closed sets as StrEnum, records as frozen dataclasses
world.py       the mutable accounts; the only place a balance or frozen flag changes
operations.py  the six operations, each with materiality, a check, and an effect
policy.py      the six numbered rules; the first that matches decides a step
engine.py      drives a request through its plan, requesting and resolving approvals
store.py       SQLite: requests, steps, approvals, accounts, and the append-only log
export.py      a registry of exporters keyed by ExportFormat
api.py         the HTTP surface over the store
cli.py         argument parsing and the single place AppError becomes a message
```

Nothing past `parsing.py` sees a `dict[str, Any]`.
The engine works in terms of `ServiceRequest`, `OperationCall`, and enum members.

### Adding things

- A new operation: add an `OperationName` member and one `Operation` entry in `OPERATIONS`.
- A new export format: add an `ExportFormat` member and one entry in `EXPORTERS`.
- A new policy rule: add a `PolicyRule` to `RULES` at the position its precedence requires.

### Design notes

- **Amounts are `Decimal` throughout**, parsed from strings and formatted only at the boundary,
  so no binary-float rounding reaches a balance.
- **The log is only ever appended to.** No code path updates or deletes a row in `log`.
  A test asserts the earlier entries are unchanged after later activity.
- **Approval is per step, not per request.** A request that clears one approval can stop again at
  a later step, which is what `account_recovery` does.
- **Two notification templates stay a two-case `if`.** The spec says these are the only two and
  are not expected to change, so a registry would add indirection without removing complexity.
- **`show` memoises by reference** in `Store.view_request`. The cache is invalidated on any write
  touching that reference, so it cannot serve stale data.
- **Failures split in two.** `OperationFailure` means an operation legitimately could not run and
  fails its request; `AppError` means the caller gave bad input and exits non-zero with a message.
  A traceback never reaches the terminal.
