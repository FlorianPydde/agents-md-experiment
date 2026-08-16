# Governed Service Request Runner

A small back office prototype: it receives service requests, turns each into
a plan of steps, and runs those steps under policy control. Some steps run
on their own; others need a named role's approval. Everything that happens
is written to an append only SQLite log.

## Running it

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/). From this
folder:

```bash
uv sync
uv run python -m app run scenario.json --world world.json
```

This starts from a clean `log.db` in this folder, replays `scenario.json`
against the starting state in `world.json`, and prints a summary: one line
per request in arrival order, then one line per account sorted by id.

Other commands (they read the `log.db` left behind by `run`):

```bash
uv run python -m app show REQ-1002 [REQ-1003 ...]   # request detail, steps, approvals, log
uv run python -m app export --format csv            # writes report.csv / .json / .tsv
uv run python -m app serve --port 8000               # HTTP API
```

## Tests

```bash
uv run pytest
```

## How the pieces fit together

- **`app/models.py`** — validated views over the JSON that arrives from
  outside (`world.json`, `scenario.json`): accounts, intake requests and
  decide entries. All input validation and clear error messages live here.
- **`app/plans.py`** — the fixed, ordered list of operations for each request
  `kind`.
- **`app/operations.py`** — the six supported operations. Each has a
  materiality (`read`/`write`), a check that validates its arguments and the
  account state, and an effect that mutates the in-memory account state and
  reports what happened.
- **`app/policy.py`** — the ordered policy rules that decide, for a given
  operation and its arguments, whether it may run on its own or which role
  must approve it.
- **`app/storage.py`** — the SQLite persistence layer: accounts, requests,
  steps, pending/resolved approvals, and the append only log. `run` always
  starts from a fresh database file (`log.db`); every other command opens the
  existing one and fails clearly if it is missing.
- **`app/engine.py`** — ties the above together. `intake()` records a new
  request, builds its plan, and advances it as far as policy allows.
  `decide()` resolves a pending approval and, on approval, continues
  advancing the request. A request becomes `awaiting_approval` when a step
  needs a role's sign off, `rejected` when that sign off is refused,
  `failed` when an operation's check fails (for example, a debit against a
  frozen account or an insufficient balance), and `completed` once its last
  step finishes. Every occurrence — a request arriving, a plan being made, a
  policy decision, an approval being requested or resolved, an operation
  running or failing, and a request reaching its final state — is appended
  to the log.
- **`app/cli.py`** / **`app/__main__.py`** — the `run`, `show`, `export` and
  `serve` commands. All expected failures (missing/malformed files, invalid
  values, decisions naming nothing pending or the wrong role, and so on)
  raise `AppError` and are reported as a single line on stderr with a
  non-zero exit status, never a traceback.
- **`app/api.py`** — a small HTTP API (standard library `http.server`, no
  extra dependency) built directly on `Storage`/`Engine`. It offers a health
  check, listing/fetching requests, listing pending approvals, resolving an
  approval, fetching a request's log entries, and listing the operations and
  policy rules.

## Design notes

- Amounts and balances are parsed as `decimal.Decimal` from the strings in
  the JSON documents to keep exact decimal arithmetic, and are always
  serialized back out to two decimal places.
- Timestamps never appear in any command output or in the data recorded in
  the log, so repeated runs against the same input produce identical text.
- `show` accepts multiple references in one invocation and de-duplicates
  repeated ones so the underlying lookup only happens once per reference.
