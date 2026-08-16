# Governed Service Request Runner

A small back office prototype. It turns incoming service requests into a
plan of steps, runs those steps under policy control (some run on their
own, some need approval from a named role), and records every meaningful
occurrence to an append only log held in SQLite.

## Running it

Requires Python 3.12 and [`uv`](https://docs.astral.sh/uv/).

```bash
cd experiment/control/claude-sonnet-5

# Replay the supplied scenario from a clean database and print the summary.
uv run python -m app run scenario.json --world world.json

# Show one or more requests: their fields, steps, approvals and log entries.
uv run python -m app show REQ-1002 REQ-1004

# Export a report of every request.
uv run python -m app export --format csv    # writes report.csv
uv run python -m app export --format json   # writes report.json
uv run python -m app export --format tsv    # writes report.tsv

# List the supported operations / policy rules.
uv run python -m app operations
uv run python -m app policy

# Serve the HTTP API.
uv run python -m app serve --host 127.0.0.1 --port 8000
```

Run the tests with:

```bash
uv run pytest
```

## How the pieces fit together

| Module | Responsibility |
|---|---|
| `app/models.py` | Shared constants (kinds, states, plans, operations) and amount parsing/validation. |
| `app/policy.py` | The policy rules: whether a step runs on its own or needs a named role's approval. |
| `app/operations.py` | The six operations: their checks and effects on an account. |
| `app/storage.py` | The SQLite schema, connection handling, and the append only log. |
| `app/engine.py` | Orchestration: intake a request, plan it, run steps until an approval, a failure, or completion; resolve a pending approval (`decide`). |
| `app/cli.py` | The `run`, `show`, `export`, `serve`, `operations` and `policy` commands. |
| `app/export.py` | Writes `report.csv` / `report.json` / `report.tsv`. |
| `app/api.py` | The HTTP API served by `serve`. |

### Data model

The SQLite database (`governed.db` by default, in this folder) holds:

- `accounts` - current balance, tier and frozen state.
- `requests` - one row per request: its kind, account, amount, requester,
  current lifecycle state, and which step it is up to.
- `steps` - the ordered plan for each request and each step's state
  (`pending`, `done`, `failed`, `skipped`).
- `approvals` - pending and resolved approvals, naming the required role.
- `log` - the append only log. Every entry records an event type, the
  request it belongs to (when relevant), and ordered JSON data about what
  happened. Entries are never edited or deleted. No timestamp is ever
  printed by any command, so repeated `run`s produce identical text; an
  internal timestamp is still stored in the log row for later inspection.

### Request lifecycle

`received` -> (optionally `awaiting_approval`, any number of times, one
per step that needs approval) -> one of `completed`, `rejected`, `failed`.

`run` starts from a clean database (any existing database file is
removed) and replays `scenario.json` in order: each `intake` entry creates
a request and runs it as far as policy allows; each `decide` entry
resolves the single pending approval for the named request, naming the
role that decided and whether it approved or rejected.

### HTTP API (`serve`)

- `GET /health`
- `GET /requests`, `GET /requests/<reference>`
- `GET /requests/<reference>/log`
- `GET /approvals` (pending approvals)
- `POST /approvals/<reference>` with a JSON body `{"role": "...", "decision": "approve"|"reject"}`
- `GET /operations`
- `GET /policy`

### Errors

Anything the operator caused - a missing or malformed file, a value
outside the allowed sets, a negative amount, a missing field, a decision
naming a request with nothing pending, or a decision from the wrong role
- is reported as a one line message on stderr and a non zero exit status,
never a traceback. See `app/errors.py::AppError`.
