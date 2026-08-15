# Governed Service Request Runner

A back-office prototype that receives service requests, turns each into a
plan of operations, and runs those operations under policy control - some
run automatically, others need approval from a named role. Every meaningful
occurrence is written to an append-only log held in SQLite.

## Running it

This project uses `uv` and Python 3.12.

```powershell
cd experiment/control_claude-sonnet-5

# Replay the supplied scenario from a clean database and print the summary
uv run python -m app run scenario.json --world world.json

# Inspect one or more requests in detail (state, steps, approvals, log)
uv run python -m app show REQ-1002

# Write a report of all requests
uv run python -m app export --format csv    # or json / tsv

# Serve the HTTP API
uv run python -m app serve --port 8000

# Run the tests
uv run pytest -q
```

`run` always starts from a fresh `db.sqlite3` in the current directory (any
existing file is deleted first). `show`, `export`, and `serve` operate on
whatever database `run` last produced.

## How the pieces fit together

- **`app/domain.py`** - shared constants (request kinds, states, plans) and
  the `Decimal` amount parser/formatter, so every amount in the system is an
  exact decimal, never a float.
- **`app/loaders.py`** - reads and validates `world.json` and `scenario.json`
  into typed dataclasses. All "malformed input" errors are raised here as
  `ValidationError`.
- **`app/operations.py`** - the six supported operations
  (`read_account`, `apply_credit`, `apply_debit`, `freeze_account`,
  `unfreeze_account`, `notify_customer`). Each knows its materiality
  (`read`/`write`), a `check` that validates it can run, and a `run` that
  performs the effect against an in-memory account row.
- **`app/policy.py`** - the ordered policy rule table. Given an operation,
  its materiality, and its arguments, decides whether it runs on its own or
  needs a named role's approval.
- **`app/store.py`** - the SQLite-backed persistence layer: accounts,
  requests, per-request plan steps, pending/resolved approvals, and the
  append-only log. Nothing here is ever mutated destructively except account
  balances/frozen flags and request/step state, which reflect current
  progress; the log itself is insert-only.
- **`app/engine.py`** - the request lifecycle. `intake()` creates a request,
  builds its plan from `PLANS`, and runs steps until it hits an approval
  gate, a failure, or completion. `decide()` resolves a pending approval
  (approve continues the plan; reject ends it) and validates that the
  decision names a real pending approval from the correct role.
- **`app/commands/`** - one module per CLI subcommand (`run`, `show`,
  `export`, `serve`), each a thin driver over `Engine`/`Store`.
- **`app/__main__.py`** - dispatches `python -m app <command>` to the right
  command module and turns any `AppError` into a clean one-line message on
  stderr with a non-zero exit code, never a traceback.

## Design notes

- **Timestamps** never appear anywhere in the log or any command output, so
  repeated runs of the same scenario produce byte-identical text. The log's
  ordering is tracked with a monotonically increasing `seq` integer instead
  of a wall-clock time.
- **`show` caching**: within a single `show` invocation, each reference is
  looked up from the database only once, even if it is repeated on the
  command line; the second mention re-prints the cached result.
- **Errors**: every place the spec calls out (missing/malformed files, a
  value outside its allowed set, negative amounts, missing required fields,
  a decision naming a request with nothing pending, or a decision from the
  wrong role) raises an `AppError` subclass that `__main__.py` turns into a
  clean `error: ...` message and exit code 1.
- **HTTP API** (`serve`): built on the standard library's `http.server` to
  avoid adding a web framework dependency, since the scope here is a handful
  of read routes plus one write route (resolving an approval). Routes:
  `GET /health`, `GET /requests`, `GET /requests/<reference>`,
  `GET /requests/<reference>/log`, `GET /approvals/pending`,
  `POST /approvals/<reference>/resolve`, `GET /operations`, `GET /policy`.
