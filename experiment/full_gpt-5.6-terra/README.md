# Governed Service Request Runner

Run the supplied scenario from this directory with:

```powershell
uv run python -m app run scenario.json --world world.json
```

`run` recreates `governed_service.sqlite3`, initializes account state, replays the scenario, and prints its deterministic summary.
`show REQ-1002` displays a persisted request, steps, approvals, and audit log.
`export --format csv` writes a request report in CSV, JSON, or TSV.
`serve` starts the built-in HTTP API on `127.0.0.1:8000`.

The `app` package separates validated input models, immutable domain values, a registry of supported operations, SQLite persistence, and the orchestration service.
The SQLite audit table is append-only: no application statement updates or deletes audit entries.
