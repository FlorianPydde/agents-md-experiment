# Governed Service Request Runner

Run the supplied scenario from this directory:

```powershell
uv run python -m app run scenario.json --world world.json
```

The `run` command recreates `service_log.sqlite3`, imports the account world, and replays the ordered scenario.
It stores requests, steps, approvals, account state, and append-only events in SQLite.

Use `uv run python -m app show REQ-1002` to inspect a request, `uv run python -m app export --format csv` to create a request report, and `uv run python -m app serve` to serve the JSON API on `127.0.0.1:8000`.
The API offers `/health`, `/requests`, `/requests/{reference}`, `/requests/{reference}/log`, `/approvals/pending`, `/approvals/resolve`, `/operations`, and `/policy`.

Run the tests with:

```powershell
uv run python -m unittest discover -s tests -v
```
