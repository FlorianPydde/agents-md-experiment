from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.db import Database
from app.engine import Engine
from app.loader import load_scenario, load_world

REPO_DIR = Path(__file__).resolve().parent.parent


def build_api_client(tmp_path):
    db_path = tmp_path / "api.db"
    world = load_world(str(REPO_DIR / "world.json"))
    scenario = load_scenario(str(REPO_DIR / "scenario.json"))

    db = Database(db_path)
    db.reset()
    for account in world.accounts.values():
        db.upsert_account(account.id, account.owner, account.tier, account.balance, account.frozen)
    db.commit()

    engine = Engine(db, world)
    for entry in scenario:
        if entry["action"] == "intake":
            engine.intake(entry["request"])
        else:
            engine.decide(entry["reference"], entry["role"], entry["decision"])
        db.commit()
    db.close()

    application = create_app(str(db_path))
    return TestClient(application)


def test_health(tmp_path):
    client = build_api_client(tmp_path)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_list_and_get_requests(tmp_path):
    client = build_api_client(tmp_path)
    resp = client.get("/requests")
    assert resp.status_code == 200
    data = resp.json()
    assert [r["reference"] for r in data] == ["REQ-1001", "REQ-1002", "REQ-1003", "REQ-1004"]

    resp = client.get("/requests/REQ-1002")
    assert resp.status_code == 200
    assert resp.json()["state"] == "completed"

    resp = client.get("/requests/NOPE")
    assert resp.status_code == 404


def test_pending_approvals_and_log(tmp_path):
    client = build_api_client(tmp_path)
    resp = client.get("/approvals")
    assert resp.status_code == 200
    assert resp.json() == []  # all decided in the scenario

    resp = client.get("/requests/REQ-1002/log")
    assert resp.status_code == 200
    events = [e["event_type"] for e in resp.json()]
    assert "request_received" in events
    assert "request_final" in events


def test_operations_and_policy_listing(tmp_path):
    client = build_api_client(tmp_path)
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
    assert len(resp.json()) == 6


def test_resolve_pending_approval_via_api(tmp_path):
    # Build a fresh db with one request stuck awaiting approval.
    db_path = tmp_path / "resolve.db"
    world = load_world(str(REPO_DIR / "world.json"))
    db = Database(db_path)
    db.reset()
    for account in world.accounts.values():
        db.upsert_account(account.id, account.owner, account.tier, account.balance, account.frozen)
    db.commit()

    engine = Engine(db, world)
    engine.intake(
        {
            "reference": "REQ-API-1",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "200.00",
            "requester": {"name": "A", "role": "agent", "origin": "internal"},
        }
    )
    db.commit()
    db.close()

    application = create_app(str(db_path))
    client = TestClient(application)

    resp = client.get("/approvals")
    assert len(resp.json()) == 1

    resp = client.post(
        "/requests/REQ-API-1/decide", json={"role": "finance", "decision": "approve"}
    )
    assert resp.status_code == 200
    assert resp.json()["state"] == "completed"

    resp = client.get("/approvals")
    assert resp.json() == []
