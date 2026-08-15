# Governed Service Request Runner

A back office prototype.
It receives service requests, expands each into a plan of steps, and runs those steps under policy control.
Some steps run on their own; others stop and wait for a named role to approve them.
Everything that happens is appended to a log that is never edited or deleted.

## Running it

Requires Python 3.12 and `uv`. Dependencies install on first `uv run`.

```
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
uv run pytest
```

`run` starts from a clean database, replays the scenario, and prints the summary.
That summary is the acceptance criterion, and repeated runs produce identical text because no timestamp ever reaches the output.

`show` prints a request, its steps, its approvals, and its log entries.
It accepts several references in one call, and the per-reference lookup is memoised so asking twice does the work once.
An unknown reference exits non-zero with a message naming it.

`export` writes `report.csv`, `report.json` or `report.tsv`.
`serve` runs the HTTP API against the database the last `run` left behind.

## HTTP API

```
GET  /health
GET  /requests
GET  /requests/{reference}
GET  /requests/{reference}/log
GET  /approvals
POST /approvals/{reference}     body: {"role": "finance", "decision": "approve"}
GET  /operations
GET  /policy
```

## How the pieces fit

```
cli.py        argument parsing, exit codes, and the one place errors become messages
engine.py     intake, plan expansion, policy-gated execution, decisions, replay
policy.py     the ordered rules; first match wins
plans.py      which steps each request kind expands into
operations.py the six operations, each with materiality, validation, and effect
world.py      the accounts and the changes applied to them
store.py      SQLite: the append-only log plus the current projection
reporting.py  the run summary, the show view, and the export formats
api.py        the HTTP API
models.py     the closed sets of values and the records built from them
errors.py     the error types that surface as clean messages
```

The flow through those pieces is one loop.
`engine.intake` records the request, expands the plan for its kind, then walks the steps.
For each step it asks `policy.evaluate` whether the operation may run on its own.
If it may, `operations` runs it against `world` and the walk continues.
If it may not, the walk stops: a pending approval is recorded naming the required role and its step, and the request becomes `awaiting_approval`.
A later `decide` resolves that approval.
`approve` runs the step and resumes the walk from the next one, which may stop again at another approval.
`reject` ends the request.
An operation that refuses ends the request as `failed`.
Reaching the end of the plan completes it.

### Design notes

**Operations are a registry, not a conditional.**
Each entry carries its materiality, its own argument check, and its effect.
The engine looks operations up by name and never knows what any particular one does, so adding a seventh is a registry entry rather than an engine change.

**Policy is a list of data rules evaluated in order.**
Each rule is a predicate over the operation and the amount plus the role it demands.
The first match wins and the last rule matches everything, which makes the set total by construction.
The rules are exposed verbatim over `/policy`, so what the API reports cannot drift from what the engine enforces.

**Formats are a dictionary of renderers.**
The spec says more formats are expected, so `export` selects a renderer by name and the CLI derives its accepted choices from the same dictionary.

**The log is append only, enforced by the database.**
SQLite triggers abort any `UPDATE` or `DELETE` on the log table, so the guarantee holds even against something that bypasses the store's API.
Alongside the log the store keeps a projection of requests, steps, approvals and accounts.
That is what lets `show`, `export` and `serve` work against a database written by an earlier `run` without replaying the scenario.

**No timestamps anywhere in output.**
The log orders itself by an autoincrementing sequence number instead of a clock, which is what makes runs byte-identical.

**Money is `Decimal` throughout.**
Amounts arrive as strings and stay exact; they become text again only at the edges.

**Errors are a small hierarchy caught once.**
Everything user-facing derives from `AppError`, and `cli.main` is the single place that turns one into a message and a non-zero exit.
Nothing else prints and nothing reaches the user as a traceback.

## Tests

`tests/test_policy.py` covers each rule and its boundary, including 100.00 running on its own and 100.01 needing finance.
`tests/test_engine.py` covers the approval flow end to end - stopping, approving, resuming, rejecting, stopping twice within one recovery plan - and the failing operations: a debit beyond the balance and a write against a frozen account.
It also covers the refusals: wrong role, nothing pending, unknown reference, negative amount, missing field, unknown kind.
`tests/test_cli.py` asserts the acceptance output character for character and checks that failures carry no traceback.
`tests/test_api.py` covers every endpoint.
