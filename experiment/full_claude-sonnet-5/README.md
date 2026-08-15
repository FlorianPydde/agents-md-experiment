# Governed Service Request Runner

A small back-office system that turns incoming service requests into plans
of steps, runs those steps under policy control (some run on their own,
others need approval from a named role), and records everything in an
append-only log held in SQLite.

## Running it

Requires Python 3.12+ and `uv`.

```
uv run python -m app run scenario.json --world world.json
```

Starts from a clean database (`log.db` by default, overwritten on `run`),
replays `scenario.json` against the starting state in `world.json`, and
prints a final summary: one line per request in arrival order, then one
line per account sorted by id.

Other commands:

```
uv run python -m app show REQ-1002 [REQ-1004 ...]
uv run python -m app export --format csv   # or json / tsv
uv run python -m app serve                 # HTTP API on 127.0.0.1:8000
```

`show` prints a request's details, its steps and their state, its
approvals, and its log entries. It accepts several references in one call;
repeated references are only looked up once. Asking for an unknown
reference exits non-zero with a message naming it (other requested
references are still shown).

`export` writes `report.csv`, `report.json`, or `report.tsv` in this
folder, one record per request (reference, kind, final state, account,
amount).

`serve` exposes:

- `GET /health`
- `GET /requests`, `GET /requests/{reference}`
- `GET /approvals` (pending approvals)
- `POST /requests/{reference}/decide` with `{"role": ..., "decision": ...}`
- `GET /requests/{reference}/log`
- `GET /operations`, `GET /policy`

Run the tests with:

```
uv run pytest
```

## How the pieces fit together

- `app/policy.py` - pure data and functions: request kinds, their step
  plans, the six operations and their materiality, the notify-customer
  templates, and the ordered policy rules that decide whether a step needs
  approval and by which role. No I/O.
- `app/operations.py` - the `World` (in-memory accounts) plus the check and
  effect for each of the six operations. Raises `OperationError` when an
  operation's arguments are bad or its effect cannot happen (e.g. debiting
  a frozen account, or below-balance debit).
- `app/db.py` - the SQLite `Database` wrapper. Holds the append-only `log`
  table (never updated or deleted from) plus current-state tables
  (`accounts`, `requests`, `steps`, `approvals`) that let commands answer
  queries without replaying the whole log.
- `app/engine.py` - the state machine that ties policy, operations, and the
  database together: `intake` validates and plans a request then runs
  steps until it stops for approval, fails, or completes; `decide` resolves
  a pending approval and, on approval, resumes the run from where it
  stopped.
- `app/loader.py` - reads and validates `world.json` and `scenario.json`,
  turning malformed input into a clear `AppError` instead of a traceback.
- `app/cli.py` - the `run` / `show` / `export` / `serve` commands.
- `app/api.py` - the FastAPI application used by `serve`.
- `app/errors.py` - `AppError`, the one exception type the CLI catches to
  print a message and exit non-zero, instead of a traceback.

### Request lifecycle

Each request is in exactly one of `received`, `awaiting_approval`,
`completed`, `rejected`, `failed`. Steps run in order; a step that may run
on its own runs immediately, a step that needs approval stops the run
there and records a pending approval naming the required role. A later
`decide` either resumes the run (`approve`) or ends the request
(`reject`). An operation failure ends the request as `failed` with no
further steps run. Every meaningful occurrence - a request arriving, a
plan being made, a policy decision, an approval being requested or
resolved, an operation running or failing, and a request reaching a final
state - is appended to the log. Timestamps never appear anywhere in
command output, so repeated runs of the same scenario against the same
starting world produce identical text.
