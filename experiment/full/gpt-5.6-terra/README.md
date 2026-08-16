# Governed Service Request Runner

Use Python 3.12 and `uv` to run the application from this directory:

```sh
uv run python -m app run scenario.json --world world.json
uv run python -m unittest discover -s tests
```

`run` recreates `service.db`, imports the world, and replays the scenario. The SQLite database
stores accounts, request plans, approvals, and the append-only event log. `show REFERENCE` prints
the stored request detail; `export --format csv|json|tsv` writes a request report; and `serve`
starts the JSON HTTP API (health, requests, approvals, logs, operations, and policy rules).
