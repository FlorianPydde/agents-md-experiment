# Governed Service Request Runner

This prototype plans and executes account service requests under approval policy.
It persists account state, request state, approvals, ordered steps, and an append-only event log in `service.db`.

## Run

Use Python 3.12 and `uv` from this directory.

```powershell
uv sync
uv run python -m app run scenario.json --world world.json
uv run python -m app show REQ-1002
uv run python -m app export --format csv
uv run python -m app serve
```

The API is served at `http://127.0.0.1:8000`.
Interactive API documentation is available at `/docs`.

## Design

`app/domain.py` defines enums, immutable domain records, money invariants, and domain errors.
`app/wire.py` validates all external JSON with strict Pydantic models before conversion to domain objects.
`app/engine.py` contains the complete operation registry, policy evaluation, plans, and request lifecycle.
`app/storage.py` owns the SQLite schema and persistence operations.
SQLite triggers reject updates and deletes against the event log.
`app/presentation.py` builds request views and dispatches report writers through an enum-keyed registry.
`app/api.py` exposes the HTTP API, while `app/__main__.py` implements the four commands.

Run all tests with:

```powershell
uv run pytest
```
