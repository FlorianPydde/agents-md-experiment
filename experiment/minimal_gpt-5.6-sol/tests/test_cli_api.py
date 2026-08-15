import json
import os
import subprocess
import sys
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from app.api import create_handler
from app.models import load_scenario, load_world
from app.storage import Repository
from app.workflow import Runner


ROOT = Path(__file__).parents[1]
EXPECTED = (
    "REQ-1001  goodwill_credit     completed\n"
    "REQ-1002  goodwill_credit     completed\n"
    "REQ-1003  account_recovery    rejected\n"
    "REQ-1004  collect_debt        failed\n"
    "ACC-100      1500.00  active\n"
    "ACC-200        50.00  frozen\n"
)


def test_run_matches_acceptance_output_exactly(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app",
            "run",
            str(ROOT / "scenario.json"),
            "--world",
            str(ROOT / "world.json"),
        ],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == EXPECTED


def populated_repository(path: Path) -> None:
    with Repository(path) as repository:
        repository.reset(load_world(ROOT / "world.json"))
        runner = Runner(repository)
        for entry in load_scenario(ROOT / "scenario.json")[:2]:
            runner.intake(entry.request)


@pytest.fixture
def api_server(tmp_path):
    database = tmp_path / "api.db"
    populated_repository(database)
    server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(database))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()
    server.server_close()


def get_json(url: str):
    with urllib.request.urlopen(url) as response:
        return response.status, json.load(response)


def test_http_api_lists_resources_and_policy(api_server):
    status, health = get_json(f"{api_server}/health")
    assert status == 200
    assert health == {"status": "ok"}

    _, requests = get_json(f"{api_server}/requests")
    assert [request["reference"] for request in requests] == ["REQ-1001", "REQ-1002"]

    _, approvals = get_json(f"{api_server}/approvals")
    assert approvals[0]["required_role"] == "finance"

    _, operations = get_json(f"{api_server}/operations")
    assert len(operations) == 6

    _, policy = get_json(f"{api_server}/policy")
    assert len(policy["rules"]) == 6

    _, log = get_json(f"{api_server}/requests/REQ-1002/log")
    assert any(entry["event"] == "approval_requested" for entry in log)


def test_http_api_resolves_approval(api_server):
    request = urllib.request.Request(
        f"{api_server}/approvals/REQ-1002",
        data=json.dumps({"role": "finance", "decision": "approve"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request) as response:
        body = json.load(response)
    assert body["state"] == "completed"
