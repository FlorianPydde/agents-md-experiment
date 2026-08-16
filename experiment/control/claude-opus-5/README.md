# Governed Service Request Runner

A back office prototype. It receives service requests, turns each into a plan of steps, and runs
those steps under policy control. Steps that policy considers material stop and wait for a named
role to approve them. Everything that happens is appended to a log that is never edited.

## Running it

Python 3.12, no third party packages.

```
uv run python -m app run scenario.json --world world.json   # replay from a clean database
uv run python -m app show REQ-1002 REQ-1003                 # one or more requests in full
uv run python -m app export --format csv                    # report.csv, report.json, report.tsv
uv run python -m app serve                                  # HTTP API on 127.0.0.1:8000
uv run python -m unittest discover -s tests -t tests        # the tests
```

`python` works in place of `uv run python` if the interpreter is already 3.12.

`run` starts from an empty database and prints one line per request in arrival order, then one
line per account by id. No output ever carries a timestamp, so repeated runs print identical
text. The other commands read the database that `run` left behind.

Every error, whether a missing file, a value outside a permitted set, a negative amount, or a
decision from the wrong role, exits non zero with a single line on standard error and no
traceback.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness and how many requests are stored |
| GET | `/requests` | every request with its final state |
| GET | `/requests/{reference}` | one request with steps, approvals and log |
| GET | `/requests/{reference}/log` | the log entries for a request |
| GET | `/approvals` | the pending approvals |
| POST | `/approvals/{reference}` | resolve one, body `{"role": ..., "decision": ...}` |
| GET | `/operations` | the supported operations and their materiality |
| GET | `/policy` | the policy rules in order |

## How the pieces fit together

```
loader  ->  engine  ->  operations  ->  world
              |  \                        |
              |   policy                store  ->  reporting / export / api
```

- `loader.py` reads `world.json` and `scenario.json` and rejects anything malformed, so nothing
  downstream has to re check a value.
- `models.py` holds the fixed vocabulary: kinds, states, roles, materiality, and the plan of each
  kind.
- `plans.py` expands a request into its ordered steps and the arguments each step needs.
- `policy.py` holds the rules as an ordered list; the first match wins and the result names the
  rule, so the log explains itself.
- `operations.py` holds the six operations in a registry. Each declares its materiality, checks
  its own arguments, and applies its effect. The frozen account guard lives in the base class, so
  it applies to every write except `unfreeze_account`. A seventh operation is one class and one
  decorator.
- `world.py` is the accounts and the changes made to them.
- `engine.py` runs a plan: it evaluates policy for each step, runs the step or stops and records
  a pending approval, and finalises the request on completion, rejection or failure. It keeps no
  state of its own, so a later command or the API can pick a request up where it stopped.
- `store.py` is the SQLite database: accounts, requests, steps, approvals and the log. Triggers
  refuse updates and deletes on the log table, so the append only guarantee does not rely on
  callers behaving.
- `reporting.py` renders the summary and the `show` output, and caches lookups so asking twice
  for one reference does the work once.
- `export.py` writes one record per request through a registry of format writers.
- `api.py` serves the same store over HTTP, single threaded so there is one SQLite connection and
  no locking to reason about.

An operation that cannot be applied, such as debiting more than the balance, raises
`OperationFailure` and moves the request to `failed`. Everything the caller got wrong raises
`AppError` and reaches the command line as one clear message.

## Generated files

`ledger.db` and `report.*` are produced by the commands and are not part of the source.
