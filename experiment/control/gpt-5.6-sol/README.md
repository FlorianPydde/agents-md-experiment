# Governed Service Request Runner

This Python 3.12 prototype turns incoming service requests into ordered plans, evaluates each
operation against policy, pauses for role-based approvals, executes approved operations, and
records every occurrence in an append-only SQLite log.

## Run

From this directory:

```sh
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
```

The `run` command recreates `service.db` and replays the supplied files. The other commands use
that persisted database. Export supports `csv`, `json`, and `tsv`.

Run the tests with:

```sh
uv run python -m unittest discover -v
```

## HTTP API

The server listens on `127.0.0.1:8000` by default. It accepts `--host` and `--port`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/requests` | List requests |
| `GET` | `/requests/{reference}` | Fetch a request and its steps and approvals |
| `GET` | `/requests/{reference}/log` | Fetch the request log |
| `GET` | `/approvals` | List pending approvals |
| `POST` | `/requests/{reference}/decisions` | Resolve an approval with `role` and `decision` JSON fields |
| `GET` | `/operations` | List supported operations |
| `GET` | `/policy` | List policy rules in matching order |

## Structure

- `app/service.py` validates input and owns planning, policy, approval, and operation execution.
- `app/db.py` owns the SQLite schema, persistence, request views, and append-only event log.
- `app/cli.py` implements the four commands and report writers.
- `app/api.py` exposes the service through a small JSON HTTP API.
- `tests/` covers all policy branches, approval outcomes, validation, and operation failure.

