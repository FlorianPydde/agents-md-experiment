# Governed Service Request Runner

Run the supplied scenario with `uv run python -m app run scenario.json --world world.json`.
This resets `service.db`, replays the scenario, and prints the final request and account summary.

Use `uv run python -m app show REQ-1002` to inspect requests and their append-only event
log, or `uv run python -m app export --format csv` to create a report. `uv run python -m app
serve` starts the JSON API on port 8000. It exposes `/health`, `/requests`, `/requests/{reference}`,
`/requests/{reference}/log`, `/approvals`, and `/operations`; resolve an approval with
`POST /approvals/{reference}` (or `/approvals/{reference}/decide`) containing `role` and
`decision`.

The runner persists accounts, request plans, approvals, and events in SQLite. It evaluates each
planned step in order, records the policy decision, then either executes it or waits for the
required role to decide.
