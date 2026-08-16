"""`python -m app run scenario.json --world world.json`

Starts from a clean database, replays the scenario, and prints the final
summary described in SPEC.md.
"""

from __future__ import annotations

from app.engine import Engine
from app.reporting import run_summary_lines
from app.scenario import load_scenario

DEFAULT_DB_PATH = "log.db"


def run_command(scenario_path: str, world_path: str, db_path: str = DEFAULT_DB_PATH) -> str:
    actions = load_scenario(scenario_path)
    engine = Engine.fresh(world_path, db_path)
    engine.run_scenario(actions)
    return "\n".join(run_summary_lines(engine))
