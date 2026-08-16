# Governed Service Request Runner

Service requests arrive from an intake system, become an ordered plan of steps, and run under
policy control. Steps that policy allows run immediately; steps that need a named role stop the
run until that role decides. Everything that happens is appended to a SQLite log that is never
edited or deleted.

## Running it

Python 3.12 with [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002 REQ-1004
uv run python -m app export --format csv
uv run python -m app serve
uv run pytest
```

`run` starts from a clean database, replays the scenario and prints the summary:

```
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
```

No output carries a timestamp, so repeated runs print identical text.

The other commands read the database left behind by `run`. `--db PATH` selects a different
database; the default is `ledger.db` beside this README.

## Commands

| Command | What it does |
|---|---|
| `run SCENARIO --world WORLD` | Clean database, replay the scenario, print the summary |
| `show REF [REF ...]` | Print a request, its steps, its approvals and its log entries. A repeated reference is looked up only once. Unknown references exit non zero |
| `export --format csv\|json\|tsv [--into DIR]` | Write `report.csv`, `report.json` or `report.tsv`, one record per request |
| `serve [--host H] [--port P]` | Serve the HTTP API |

## HTTP API

| Route | Purpose |
|---|---|
| `GET /health` | Liveness and the database in use |
| `GET /requests` | Every request with its steps and state |
| `GET /requests/{reference}` | One request, 404 when unknown |
| `GET /requests/{reference}/log` | The log entries for that request |
| `GET /approvals` | The pending approvals |
| `POST /approvals/{reference}` | Resolve the pending approval: `{"role": "finance", "decision": "approve"}` |
| `GET /operations` | The supported operations and their materiality |
| `GET /policy` | The policy rules in the order they are tried |

## How the pieces fit

| Module | Responsibility |
|---|---|
| `app/domain.py` | The vocabulary. Every fixed set is a `StrEnum`; every record is a frozen dataclass |
| `app/loading.py` | The edge. `world.json` and `scenario.json` become validated records or a clear error |
| `app/planner.py` | The kind of a request maps to its ordered steps and their arguments |
| `app/operations.py` | The six operations: materiality, argument check, effect. Held in a registry keyed by `OperationName` |
| `app/policy.py` | The ordered rules. The first match decides whether a step runs alone or which role must approve |
| `app/engine.py` | Intake, policy, running steps, pausing at approvals, resolving them, final states |
| `app/store.py` | SQLite persistence. Triggers refuse any update or delete on the log table |
| `app/reporting.py` | The summary lines and the `show` rendering, with per reference caching |
| `app/exporting.py` | Writers keyed by `ExportFormat`; a new format is one entry in that registry |
| `app/api.py` | The HTTP API over the same engine and store |

A run of one request reads: intake records the request and its plan, then for each step policy is
consulted. A step that runs alone is executed against the ledger and logged. A step that needs a
role pauses the request as `awaiting_approval` and records a pending approval. A later decision
either runs that step and continues, or rejects the request. A failing operation fails the
request. Nothing after a rejection or a failure runs.

## Errors

Bad input never produces a traceback. A missing or malformed file, a value outside the allowed
sets, a negative amount, an absent field, a decision for a request with nothing pending, and a
decision from the wrong role all exit with status 1 and a message on standard error.

## Tests

`tests/` covers the policy rules one by one, the approval flow including rejection and a second
approval in the same request, failing operations (frozen account, balance below the amount),
input validation, the append only guarantee of the log, the exact acceptance output, the export
formats and the HTTP API.
