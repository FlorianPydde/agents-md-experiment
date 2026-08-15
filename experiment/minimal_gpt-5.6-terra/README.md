# Governed Service Request Runner

Run the supplied scenario from this directory with:

```powershell
uv run python -m app run scenario.json --world world.json
```

`run` recreates the SQLite database, imports the world, replays the scenario, and prints the final request and account summaries.
The database is `service_requests.sqlite3` and retains the requests, account state, approvals, steps, and append-only event log after the command finishes.

Use `uv run python -m app show REQ-1002` to inspect persisted request detail and logs.
Use `uv run python -m app export --format csv` to create `report.csv`; `json` and `tsv` are also available.
Use `uv run python -m app serve` to start the API on `127.0.0.1:8000`.

The application validates JSON at its input boundary into frozen domain records.
`Runner` turns a request kind into an ordered plan, asks policy whether each step needs a role approval, and executes operation handlers in order.
`Store` persists all state and appends events to SQLite.
