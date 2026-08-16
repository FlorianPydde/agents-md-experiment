# Governed Service Request Runner

A back office prototype. Service requests arrive from an intake system, each becomes a plan of
steps, and the steps run under policy control. Steps that policy considers material stop for
approval by a named role. Everything that happens is appended to a SQLite log that is never
edited or deleted.

## Running it

Python 3.12 and [uv](https://docs.astral.sh/uv/). From this folder:

```bash
uv sync

uv run python -m app run scenario.json --world world.json   # replay from a clean database
uv run python -m app show REQ-1002                          # one request in full
uv run python -m app export --format csv                    # report.csv (also json, tsv)
uv run python -m app serve                                  # HTTP API on 127.0.0.1:8000

uv run pytest                                               # the test suite
```

`run` starts from a clean database, replays `scenario.json` against `world.json`, and prints
the final summary. Every command accepts `--database` to point at another SQLite file; the
default is `service_log.db` in this folder. No command prints a timestamp, so repeated runs
produce identical text.

## How the pieces fit together

| Module | Holds |
|---|---|
| `app/enums.py` | Every closed set: kinds, states, operations, roles, decisions, event kinds. |
| `app/domain.py` | `Money`, `Account`, `ServiceRequest`, `Approval` and friends. Frozen dataclasses that enforce their own invariants. |
| `app/wire.py` | The edge. Pydantic models parse `world.json`, `scenario.json` and HTTP bodies, then hand domain objects on. Unvalidated data goes no further. |
| `app/operations.py` | One registry entry per operation, holding its materiality, how its arguments are built, the check made before it runs, and the effect. Adding an operation is one entry. |
| `app/plans.py` | The ordered steps each request kind expands into. |
| `app/policy.py` | The ordered rules. The first one that applies decides whether a step runs alone or needs a role. |
| `app/engine.py` | The runner: advance through steps, stop at approvals, resolve decisions, fail on an operation failure, log everything. |
| `app/store.py` | SQLite. Accounts, requests, steps, approvals, and the log. Triggers refuse any update or delete on the log. |
| `app/views.py` | Reading the store back for `show` and for the run summary. |
| `app/reports.py` | One renderer per export format, in a registry keyed by the format. |
| `app/api.py` | The HTTP API. |
| `app/cli.py` | One registry entry per command. |

Registries are keyed by an enum and checked for coverage at import, so a new operation, plan,
export format or command that is not wired up stops the process at start rather than surprising
a caller later.

## The flow of one request

1. `intake` records the request, expands the plan for its kind, and logs both.
2. For each step, policy is asked. The verdict is logged.
3. A step that runs on its own runs immediately; the account it changed is written back and the
   run continues.
4. A step that needs a role parks the request in `awaiting_approval` with a pending approval
   naming the role and the step.
5. A later decision resolves it. `approve` runs the step and carries on, possibly stopping at
   another approval. `reject` ends the request as `rejected`.
6. An operation that refuses (a frozen account, a balance below the debit) ends the request as
   `failed`. The last step finishing ends it as `completed`.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness. |
| GET | `/requests` | Every request, in arrival order. |
| GET | `/requests/{reference}` | One request with its steps and approvals. |
| GET | `/requests/{reference}/log` | The log entries of one request. |
| GET | `/approvals` | The pending approvals. |
| POST | `/approvals/{reference}` | Resolve one, with `{"role": ..., "decision": ...}`. |
| GET | `/operations` | The supported operations and their materiality. |
| GET | `/policy` | The policy rules in order. |

An unknown reference answers `404`. A refused decision answers `409` with a message.

## Errors

Bad input never reaches a traceback. A missing or malformed file, a value outside a known set, a
negative amount, an absent field, a decision on a request with nothing pending, and a decision
from the wrong role all exit with status 1 and a single line on stderr.
