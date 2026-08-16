# Governed Service Request Runner

A small back office system. It takes service requests, turns each into a
plan of steps, and runs those steps under policy control: some run on their
own, others need a named role to approve them. Every meaningful occurrence
is written to an append only log held in a SQLite database.

## Running it

This project uses `uv` and Python 3.12. From this folder:

```
uv sync
```

### `run`

Starts from a clean database, replays `scenario.json` against the starting
state in `world.json`, and prints a summary: one line per request in the
order it arrived, then one line per account sorted by id.

```
uv run python -m app run scenario.json --world world.json
```

### `show`

Prints a request's details, its steps and their state, its approvals, and
its log entries. Accepts more than one reference; looking up the same
reference twice only does the underlying lookup once.

```
uv run python -m app show REQ-1002
```

### `export`

Writes `report.csv`, `report.json` or `report.tsv` with one record per
request: reference, kind, final state, account, and amount.

```
uv run python -m app export --format csv
```

### `serve`

Serves an HTTP API on `127.0.0.1:8000` (override with `--host`/`--port`):

- `GET /health`
- `GET /requests`, `GET /requests/{reference}`
- `GET /approvals` — pending approvals
- `POST /requests/{reference}/decide` — body `{"role": "...", "decision": "approve"|"reject"}`
- `GET /requests/{reference}/log`
- `GET /operations`, `GET /policy`

```
uv run python -m app serve
```

All commands read and write the same SQLite database, `log.db` in this
folder (override with the global `--db` flag), except `run`, which always
starts from a clean one.

## How the pieces fit together

- `app/world.py`, `app/scenario.py` — load and validate the two input files.
  Anything malformed, out of the allowed sets, negative, or missing raises
  `AppError`, which every command turns into a non zero exit and a clear
  message (or, for `serve`, an HTTP 4xx response).
- `app/domain.py` — the fixed rulebook: the plan for each request kind, the
  materiality of each operation, and the ordered policy rules.
- `app/operations.py` — the six operations. Each checks its own
  preconditions (frozen account, insufficient balance) and raises
  `OperationError` when it cannot complete.
- `app/db.py` — the SQLite schema and the one function that appends to the
  log. Log rows are only ever inserted.
- `app/engine.py` — ties it together: intake builds a plan and starts
  running it; running a step applies policy, executes it immediately or
  records a pending approval and stops; `decide` resolves a pending
  approval, then keeps running from there.
- `app/reporting.py` — the exact text format of the `run` summary.
- `app/cmd_run.py`, `app/cmd_show.py`, `app/cmd_export.py`, `app/cmd_serve.py`,
  `app/cli.py` — one module per command, plus the argument parser that
  dispatches to them.
- `app/api.py` — the HTTP API, a thin FastAPI layer over the same engine.

## Tests

```
uv run pytest
```

Covers the policy rules, the full approval flow (auto run, awaiting
approval, approve, reject, wrong role, nothing pending), a failing
operation (`apply_debit` against a frozen account and against an
insufficient balance), the exact `run` acceptance output, `show`'s lookup
caching, `export`, the HTTP API, and input validation.
