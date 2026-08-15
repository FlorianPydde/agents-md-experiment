# Governed Service Request Runner

A back office runner that takes service requests from an external intake system, expands each
into a plan of steps, and runs those steps under policy control.
Steps that policy considers material stop and wait for a named role to approve them.
Everything that happens is appended to a SQLite log that is never edited or deleted.

## Running it

```
uv sync
uv run python -m app run scenario.json --world world.json
```

`run` starts from a clean database, replays the scenario, and prints the final summary:

```
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
```

Other commands:

```
uv run python -m app show REQ-1002 REQ-1004     # request, steps, approvals, log
uv run python -m app export --format csv        # also json, tsv
uv run python -m app serve                      # HTTP API on 127.0.0.1:8000
```

`show` accepts several references in one run and caches each record, so asking twice does the
lookup once.
Every command accepts `--db PATH` if you want a database somewhere other than `runner.sqlite3`
in this folder.

Tests:

```
uv run pytest
```

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness |
| GET | `/requests` | every request in arrival order |
| GET | `/requests/{reference}` | one request with its steps and approvals |
| GET | `/requests/{reference}/log` | the log entries for a request |
| GET | `/approvals` | the pending approvals |
| POST | `/approvals/{reference}` | resolve one, body `{"role": ..., "decision": ...}` |
| GET | `/operations` | the supported operations and their materiality |
| GET | `/policy` | the policy rules in order |

## How the pieces fit

```
scenario.json ─┐
world.json ────┴─> wire.py ──> domain objects ──> engine.Runner ──> store.Store (SQLite)
                                                       │                    │
                                                  policy.py             log + state
                                                  plans.py                  │
                                                  operations.py             ├─> cli.py
                                                                            └─> api.py
```

- `enums.py` holds every closed set: request kinds, states, operations, roles, decisions,
  materiality, event kinds, export formats. Nothing in the system branches on a bare string.
- `values.py` holds `Money`, a non negative decimal that knows how to add, subtract and format
  itself. Because it cannot be negative by construction, no caller re-checks that.
- `domain.py` holds the frozen dataclasses the system actually reasons about: `Account`,
  `ServiceRequest`, `Step`, `Approval`, `RequestRecord`, plus the mutable `World` that owns the
  accounts. Rules that derive from a type's own data live on that type: `Account.ensure_writable`
  is the frozen account rule, `Account.debited` is the insufficient balance rule,
  `Approval.ensure_role` is the wrong role rule.
- `wire.py` is the only place untrusted JSON is touched. Pydantic models parse the files and the
  HTTP bodies, then `to_domain()` converts them. No `dict[str, Any]` travels past this module.
  The scenario entries are a discriminated union on `action`, which is the one place a `Literal`
  appears.
- `operations.py` is one registry of six entries. Each `Operation` carries its materiality, its
  argument check and its effect together, so adding an operation is one entry rather than edits
  in three chains. An import time assertion fails the process if any `OperationName` has no entry.
- `plans.py` maps each request kind to its ordered operations, guarded by the same assertion.
- `policy.py` is the ordered list of rules from the spec. Each rule carries its description, its
  test and the role it demands; `decide` returns the first match. The descriptions are what the
  log and the `/policy` endpoint show, so the rule text has exactly one source.
- `engine.py` drives a request: expand the plan, ask policy about the next step, either run it or
  stop and record a pending approval, and finalise on completion, rejection or failure. A decision
  re-enters the same loop, so a plan can stop at more than one approval.
- `store.py` owns the SQLite database. It writes the append only `log` table alongside the request
  state that log explains, and its reads return domain objects, never rows. Records are cached per
  reference and the cache is dropped on any write.
- `export.py` and `api.py` are the two output surfaces, both driven by registries so new formats
  and endpoints are additive.

## Design notes

Timestamps are deliberately absent from the log and from every command's output, so repeated runs
produce byte identical text.
Ordering is captured by the log's autoincrement id and by each request's arrival index instead.

Failures the operator can cause - a missing or malformed file, a value outside a closed set, a
negative amount, an absent field, a decision for a request with nothing pending, a decision from
the wrong role - all raise `AppError` and surface as `error: ...` on stderr with exit status 1.
A traceback never reaches the user.
