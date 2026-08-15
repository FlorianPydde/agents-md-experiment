from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.cli import _run

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "api.db"
    _run(ROOT / "scenario.json", ROOT / "world.json", db)
    return TestClient(create_app(db))


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_list_and_fetch_requests(client: TestClient) -> None:
    listing = client.get("/requests").json()
    assert [r["reference"] for r in listing] == [
        "REQ-1001",
        "REQ-1002",
        "REQ-1003",
        "REQ-1004",
    ]
    one = client.get("/requests/REQ-1002").json()
    assert one["state"] == "completed"
    assert len(one["steps"]) == 3


def test_unknown_reference_is_404(client: TestClient) -> None:
    assert client.get("/requests/REQ-0000").status_code == 404


def test_log_endpoint(client: TestClient) -> None:
    log = client.get("/requests/REQ-1004/log").json()
    assert any(entry["kind"] == "operation_failed" for entry in log)


def test_operations_and_policy(client: TestClient) -> None:
    ops = client.get("/operations").json()
    assert len(ops) == 6
    assert {"name": "read_account", "materiality": "read"} in ops
    rules = client.get("/policy").json()
    assert [r["number"] for r in rules] == [1, 2, 3, 4, 5, 6]


def test_pending_approvals_and_resolution(tmp_path: Path) -> None:
    db = tmp_path / "pending.db"
    _run(ROOT / "scenario.json", ROOT / "world.json", db)
    client = TestClient(create_app(db))
    assert client.get("/approvals").json() == []

    # Feed a fresh scenario that leaves an approval pending.
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        (ROOT / "scenario.json").read_text(encoding="utf-8").replace(
            '{ "action": "decide", "reference": "REQ-1002", "role": "finance",'
            ' "decision": "approve" },',
            "",
        ),
        encoding="utf-8",
    )
    db2 = tmp_path / "pending2.db"
    _run(scenario, ROOT / "world.json", db2)
    client2 = TestClient(create_app(db2))
    pending = client2.get("/approvals").json()
    assert [p["reference"] for p in pending] == ["REQ-1002"]
    assert pending[0]["required_role"] == "finance"

    refused = client2.post("/approvals/REQ-1002", json={"role": "risk", "decision": "approve"})
    assert refused.status_code == 400

    ok = client2.post("/approvals/REQ-1002", json={"role": "finance", "decision": "approve"})
    assert ok.status_code == 200
    assert ok.json()["state"] == "completed"
    assert client2.get("/approvals").json() == []
