"""`run` command: replay a scenario from a clean database and print a summary."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.engine import Engine
from app.loaders import DecideStep, IntakeStep, load_scenario, load_world
from app.store import Store


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app run")
    parser.add_argument("scenario", type=Path, help="path to scenario.json")
    parser.add_argument("--world", type=Path, required=True, help="path to world.json")
    return parser


def run(argv: list[str], *, db_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    accounts = load_world(args.world)
    steps = load_scenario(args.scenario)

    store = Store.fresh(db_path)
    try:
        engine = Engine(store)
        engine.seed_world(accounts)

        for step in steps:
            if isinstance(step, IntakeStep):
                engine.intake(step)
            elif isinstance(step, DecideStep):
                engine.decide(step)

        _print_summary(store)
    finally:
        store.close()


def _print_summary(store: Store) -> None:
    for row in store.list_requests():
        print(f"{row['reference']:<10}{row['kind']:<20}{row['state']}")

    for account in store.list_accounts():
        status = "frozen" if account.frozen else "active"
        print(f"{account.id:<10}{account.balance:>10.2f}  {status}")
