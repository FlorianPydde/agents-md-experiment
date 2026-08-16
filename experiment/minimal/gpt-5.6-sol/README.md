# Governed Service Request Runner

This Python 3.12 prototype plans and executes service requests under policy
control. SQLite stores accounts, requests, steps, approvals, notifications, and
an append-only event log in `service.db`.

## Run

From this directory:

```sh
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
```

Exports are written as `report.csv`, `report.json`, or `report.tsv`. The server
defaults to `127.0.0.1:8000`; `--host` and `--port` may override those values.

The API provides `GET /health`, `/requests`, `/requests/{reference}`,
`/approvals`, `/requests/{reference}/log`, `/operations`, and `/policy`.
Resolve an approval with `POST /approvals/{reference}` and a JSON body such as
`{"role":"finance","decision":"approve"}`.

## Test

```sh
uv run python -m unittest discover -s tests
```

`app/core.py` contains validation, policy, operations, workflow, and persistence.
`app/__main__.py` contains the command-line interface, export adapters, and HTTP
adapter.
