# Governed Service Request Runner

Use Python 3.12 (or `uv run`) from this directory:

```sh
python -m app run scenario.json --world world.json
python -m app show REQ-1002
python -m app export --format csv
python -m app serve
```

`run` creates a clean `service.db`, loads the accounts, and replays the scenario. The SQLite
database holds account state, requests, ordered steps, approvals, and an append-only event log.
The engine evaluates policy before every step, pauses for approvals, and records each outcome.
`show` and `export` read that persistent state; `serve` exposes it over a small JSON HTTP API.
