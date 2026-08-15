# Governed Service Request Runner

A back office prototype that receives service requests, turns each into a
plan of steps, and runs those steps under policy control.
Some steps run on their own.
Others must be approved by a named role before they may run.
Everything that happens is written to an append-only log held in a SQLite
database (`runner.db`) that survives between runs.

## Running it

This project uses `uv` and Python 3.12.

```
uv run python -m app run scenario.json --world world.json
```

Starts from a clean database, replays the scenario, and prints a final
summary: one line per request in arrival order, then one line per account
sorted by account id.

```
uv run python -m app show REQ-1002 REQ-1003
```

Prints each named request's details, steps, approvals, and log entries.
Exits non-zero if a reference is unknown.
Repeated references are only looked up once.

```
uv run python -m app export --format csv
```

Writes `report.csv` (or `report.json` / `report.tsv`) with one record per
request: reference, kind, final state, account, amount.

```
uv run python -m app serve
```

Serves an HTTP API (FastAPI/uvicorn) on `127.0.0.1:8000` offering:

- `GET /health`
- `GET /requests`, `GET /requests/{reference}`
- `GET /approvals` (pending approvals)
- `POST /requests/{reference}/approvals/resolve` with `{"role": ..., "decision": ...}`
- `GET /requests/{reference}/log`
- `GET /operations`, `GET /policy`

## How the pieces fit together

- `app/domain.py` - the domain model: `StrEnum`s for every fixed set of
  values (request kind, roles, states, operation names, ...), frozen
  dataclasses for records (`Account`, `ServiceRequest`, `Requester`, ...),
  and the fixed plan for each request kind.
- `app/loader.py` - the only place that reads raw JSON. Validates
  `world.json` and `scenario.json` at the edge and turns them into domain
  objects. Malformed or out-of-range input raises `AppError` here.
- `app/operations.py` - the six supported operations, each with a
  materiality, a check, and an effect, keyed by `OperationName` in a
  registry (`OPERATIONS`) rather than an if/elif chain.
- `app/policy.py` - the ordered policy rules that decide whether a step
  runs on its own or needs approval from a named role.
- `app/store.py` - the SQLite-backed persistence layer: accounts, requests,
  steps, approvals, and the append-only log. `run` starts from a fresh
  database; other commands open the existing one.
- `app/engine.py` - replays a scenario: intake creates a request, plans its
  steps, and runs them until an approval is needed or the plan ends;
  decide resolves a pending approval and continues (or stops) the run.
- `app/reports.py` / `app/export.py` - the `run` summary text and the
  pluggable export formats (`EXPORTERS` registry keyed by `ExportFormat`).
- `app/api.py` - the FastAPI app used by `serve`.
- `app/__main__.py` - the CLI, dispatching `run` / `show` / `export` /
  `serve` to the modules above and turning any `AppError` into a clean
  message and a non-zero exit status.

## Tests

```
uv run pytest
```

Covers the policy rules, the approval flow (approve and reject paths,
wrong-role and nothing-pending errors), at least one failing operation
(insufficient balance for `apply_debit`, and a write on a frozen account),
and an end-to-end check that `run` on the supplied `scenario.json` /
`world.json` produces the exact acceptance output.

## Design notes

- Timestamps never appear in log data or command output, so repeated runs
  produce identical text (SPEC.md requirement).
- Amounts are `Decimal`, parsed from strings at the edge; negative amounts
  are rejected.
- The log is append-only: entries are only ever inserted, never updated or
  deleted.
