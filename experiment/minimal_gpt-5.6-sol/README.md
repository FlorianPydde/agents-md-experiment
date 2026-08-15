# Governed Service Request Runner

This prototype validates service requests at the input boundary, creates a fixed plan for each request kind, evaluates every step against ordered policy rules, pauses for role-based approvals, and records all meaningful events in an append-only SQLite log.

## Run it

Use Python 3.12 through `uv` from this directory:

```powershell
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
uv run pytest
```

`run` replaces `service_requests.db` with a clean database before replaying the scenario.
`show`, `export`, and `serve` use that persisted database.
Exports are written as `report.csv`, `report.json`, or `report.tsv`.

## HTTP API

The server listens on `http://127.0.0.1:8000` by default.
It provides:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/requests` | List requests |
| `GET` | `/requests/{reference}` | Fetch one request |
| `GET` | `/requests/{reference}/log` | Fetch its event log |
| `GET` | `/approvals` | List pending approvals |
| `POST` | `/approvals/{reference}` | Resolve an approval with `role` and `decision` JSON fields |
| `GET` | `/operations` | List supported operations |
| `GET` | `/policy` | List policy rules in matching order |

## Structure

`models.py` contains enums, frozen domain records, and edge validation.
`storage.py` owns SQLite state and the protected append-only event log.
`workflow.py` contains plans, the operation registry, policy evaluation, and request execution.
`api.py` exposes the workflow through HTTP.
`__main__.py` provides the command-line interface and exporter registry.
