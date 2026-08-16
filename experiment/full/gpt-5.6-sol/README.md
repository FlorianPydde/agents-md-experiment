# Governed Service Request Runner

This Python 3.12 prototype plans and executes account service requests under
approval policy, while recording an append-only event log in
`service_requests.sqlite3`.

## Run

From this directory:

```console
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
uv run python -m unittest discover -s tests -v
```

`export` supports `csv`, `json`, and `tsv`. The server listens on
`127.0.0.1:8000` by default; `--host` and `--port` override those values.

## HTTP API

- `GET /health`
- `GET /requests` and `GET /requests/{reference}`
- `GET /requests/{reference}/log`
- `GET /approvals`
- `POST /approvals/{reference}` with
  `{"role": "finance", "decision": "approve"}`
- `GET /operations`
- `GET /policy`

## Structure

`app/core.py` contains validation, SQLite persistence, policy evaluation,
operations, and request execution. `app/api.py` exposes the HTTP interface,
and `app/__main__.py` implements the four commands. The `events` table rejects
updates and deletes so its ordered history remains append-only.
