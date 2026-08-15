# Governed Service Request Runner

This prototype turns service requests into ordered plans, evaluates every step against policy, pauses for role-based approvals, executes supported account operations, and records every meaningful event in an append-only SQLite log.

## Run it

Python 3.12 and `uv` are required.

```powershell
uv sync
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
uv run pytest
```

`run` recreates `runner.db`, loads the world, replays the scenario, and prints the deterministic final summary.
`show` reads full request, step, approval, and log details.
`export` writes `report.csv`, `report.json`, or `report.tsv`.
`serve` starts the API at `http://127.0.0.1:8000`.

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Check service health |
| `GET` | `/requests` | List requests |
| `GET` | `/requests/{reference}` | Fetch full request details |
| `GET` | `/requests/{reference}/log` | Fetch the request log |
| `GET` | `/approvals/pending` | List pending approvals |
| `POST` | `/approvals/{reference}` | Resolve an approval with `role` and `decision` JSON fields |
| `GET` | `/operations` | List supported operations |
| `GET` | `/policy` | List ordered policy rules |

## Structure

`service.py` owns validation, planning, policy-controlled progression, approvals, and operation execution.
`storage.py` owns the SQLite schema, append-only log guards, and queries.
`catalog.py` defines the operation and policy catalogs.
`cli.py` and `api.py` are interfaces over the same runner and store.
