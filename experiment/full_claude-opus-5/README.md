# Governed Service Request Runner

A back office prototype that takes service requests, plans their steps, and
runs those steps under policy control — with some steps requiring a named
role's approval before they can run. Everything that happens is appended to
a log held in a local SQLite database.

## Running it

This project uses [`uv`](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync
```

### Replay a scenario

```bash
uv run python -m app run scenario.json --world world.json
```

Starts from a clean database (`governed.db` in the current directory),
replays every `intake` and `decide` entry from `scenario.json` in order, and
prints a final summary: one line per request in arrival order, then one
line per account sorted by account id.

### Inspect a request

```bash
uv run python -m app show REQ-1002
```

Prints the request, its steps and their state, its approvals, and its log
entries. Multiple references may be given in one call; asking for the same
reference twice only looks it up once. Asking for a reference that doesn't
exist prints an error and exits non-zero.

### Export a report

```bash
uv run python -m app export --format csv   # or json, or tsv
```

Writes `report.<format>` with one row per request: reference, kind, final
state, account, and amount.

### Serve the HTTP API

```bash
uv run python -m app serve --host 127.0.0.1 --port 8000
```

Exposes:

- `GET /health`
- `GET /requests`, `GET /requests/{reference}`
- `GET /requests/{reference}/log`
- `GET /approvals` (pending approvals)
- `POST /approvals/{reference}` with `{"role": ..., "decision": "approve" | "reject"}`
- `GET /operations` — the six supported operations and their materiality
- `GET /policy` — the policy outcome for each operation

### Run the tests

```bash
uv run pytest
```

## How the pieces fit together

- `app/domain.py` — enums and frozen dataclasses for every closed-set value
  and record: accounts, requests, plans, steps, approvals, log entries.
  These types enforce their own invariants at construction (for example,
  `Money` rejects a negative amount).
- `app/wire.py` — Pydantic models that parse `world.json` and `scenario.json`
  at the edge and convert into the domain types above. Nothing past this
  module sees a raw dict describing a domain record.
- `app/plans.py` — the registry mapping a request kind to its ordered list
  of operations.
- `app/operations.py` — the registry of the six operations, each pairing a
  check (raised before running) with an effect (what happens when it runs).
- `app/policy.py` — the ordered policy rules deciding whether a step may run
  on its own or needs a named role's approval.
- `app/store.py` — the only module that touches SQL. Holds current account
  and request state plus the append-only log in SQLite, and returns/accepts
  only domain types.
- `app/engine.py` — the orchestrator: intake creates a plan and steps
  through it; each step is policy-checked, run if autonomous, or parked as a
  pending approval; a later decision resumes or stops the request.
- `app/cli.py` / `app/__main__.py` — the `run`, `show`, and `export`
  commands, plus a `serve` command that launches the HTTP API.
- `app/api.py` — the FastAPI app used by `serve`.

## Design notes

- The database file is `governed.db` in the current working directory. `run`
  deletes and recreates it so every run starts clean, as the spec requires.
  `show`, `export`, and `serve` reuse the same file so state and the log
  persist between separate invocations.
- Timestamps never appear in any command output, so repeated runs of the
  same scenario produce byte-identical text.
- A rejected approval marks its step `rejected` and the request `rejected`;
  a failing operation marks its step `failed` and the request `failed`;
  neither runs any further step.
