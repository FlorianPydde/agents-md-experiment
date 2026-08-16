"""Tests for the HTTP API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.cmd_run import run_command

REPO_DIR = Path(__file__).resolve().parents[1]


def make_client(tmp_path):
    db_path = tmp_path / "log.db"
    run_command(str(REPO_DIR / "scenario.json"), str(REPO_DIR / "world.json"), db_path=str(db_path))
    return TestClient(create_app(db_path=str(db_path)))


def test_health(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_and_get_request(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/requests")
    assert resp.status_code == 200
    references = [r["reference"] for r in resp.json()]
    assert references == ["REQ-1001", "REQ-1002", "REQ-1003", "REQ-1004"]

    resp = client.get("/requests/REQ-1002")
    assert resp.status_code == 200
    assert resp.json()["state"] == "completed"

    resp = client.get("/requests/NOPE")
    assert resp.status_code == 404


def test_pending_approvals_and_decide(tmp_path):
    db_path = tmp_path / "log.db"
    # Build a scenario with a still-pending approval.
    scenario = tmp_path / "scenario2.json"
    scenario.write_text(
        """
        {"steps": [
          {"action": "intake", "request": {"reference": "R1", "kind": "goodwill_credit",
            "account": "ACC-100", "amount": "250.00",
            "requester": {"name": "X", "role": "agent", "origin": "internal"}}}
        ]}
        """
    )
    run_command(str(scenario), str(REPO_DIR / "world.json"), db_path=str(db_path))
    client = TestClient(create_app(db_path=str(db_path)))

    resp = client.get("/approvals")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["role_required"] == "finance"

    # Wrong role is rejected with a 4xx, not a crash.
    resp = client.post("/requests/R1/decide", json={"role": "risk", "decision": "approve"})
    assert resp.status_code == 400

    resp = client.post("/requests/R1/decide", json={"role": "finance", "decision": "approve"})
    assert resp.status_code == 200
    assert resp.json()["state"] == "completed"

    resp = client.get("/approvals")
    assert resp.json() == []


def test_log_endpoint(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/requests/REQ-1002/log")
    assert resp.status_code == 200
    events = [e["event"] for e in resp.json()]
    assert "request_received" in events
    assert "request_finalized" in events


def test_operations_and_policy_endpoints(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/operations")
    assert resp.status_code == 200
    names = {op["name"] for op in resp.json()}
    assert names == {
        "read_account",
        "apply_credit",
        "apply_debit",
        "freeze_account",
        "unfreeze_account",
        "notify_customer",
    }

    resp = client.get("/policy")
    assert resp.status_code == 200
    assert set(resp.json()["approver_roles"]) == {"finance", "risk", "supervisor"}
