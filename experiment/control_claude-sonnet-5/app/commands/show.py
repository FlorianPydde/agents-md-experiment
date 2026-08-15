"""`show` command: print full detail for one or more requests by reference.

A caller may pass several references. Each is only looked up once even if it
appears more than once in argv (the spec: "asking twice ... must not repeat
the underlying lookup work").
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app.store import Store


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app show")
    parser.add_argument("references", nargs="+", help="one or more request references")
    return parser


def run(argv: list[str], *, db_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not db_path.exists():
        print(f"error: no database found at {db_path}; run 'run' first", file=sys.stderr)
        sys.exit(1)

    store = Store.open_existing(db_path)
    try:
        cache: dict[str, dict] = {}
        for reference in args.references:
            if reference not in cache:
                cache[reference] = _lookup(store, reference)
            _print_request(cache[reference])
    finally:
        store.close()


def _lookup(store: Store, reference: str) -> dict:
    request_row = store.get_request(reference)
    if request_row is None:
        print(f"error: no such request: {reference}", file=sys.stderr)
        sys.exit(1)

    steps = store.list_steps(reference)
    approvals = store.list_approvals(reference)
    log = store.list_log(reference)
    return {
        "request": request_row,
        "steps": steps,
        "approvals": approvals,
        "log": log,
    }


def _print_request(bundle: dict) -> None:
    request = bundle["request"]
    print(f"Request {request['reference']}")
    print(f"  kind: {request['kind']}")
    print(f"  account: {request['account']}")
    print(f"  amount: {request['amount']}")
    print(f"  requester: {request['requester_name']} ({request['requester_role']}, "
          f"{request['requester_origin']})")
    print(f"  state: {request['state']}")

    print("  steps:")
    for step in bundle["steps"]:
        print(f"    [{step['step_index']}] {step['operation']}: {step['state']}")

    print("  approvals:")
    if not bundle["approvals"]:
        print("    (none)")
    for approval in bundle["approvals"]:
        print(
            f"    step {approval['step_index']} requires {approval['role']}: {approval['state']}"
        )

    print("  log:")
    for entry in bundle["log"]:
        print(f"    [{entry['seq']}] {entry['event_type']}: {entry['data']}")
