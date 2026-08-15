from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.engine import Engine
from app.models import Account, Tier
from app.store import Store
from app.world import World


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "api.db"
    store = Store.fresh(db)
    accounts = [Account("ACC-100", "Ada Lovelace", Tier.STANDARD, Decimal("1200.00"), False)]
    engine = Engine(World(accounts), store)
    engine.intake(
        {
            "reference": "REQ-1",
            "kind": "goodwill_credit",
            "account": "ACC-100",
            "amount": "250.00",
            "requester": {"name": "Nia", "role": "agent", "origin": "internal"},
        },
        "req",
    )
    store.close()
    return TestClient(create_app(db))


def test_health(client: TestClient) -> None:
    assert client.get("/health").json()["status"] == "ok"


def test_list_and_fetch_requests(client: TestClient) -> None:
    listing = client.get("/requests").json()
    assert [r["reference"] for r in listing["requests"]] == ["REQ-1"]

    one = client.get("/requests/REQ-1").json()
    assert one["kind"] == "goodwill_credit"
    assert one["state"] == "awaiting_approval"

    assert client.get("/requests/REQ-NOPE").status_code == 404


def test_pending_approvals_and_resolution(client: TestClient) -> None:
    approvals = client.get("/approvals").json()["approvals"]
    assert len(approvals) == 1
    assert approvals[0]["required_role"] == "finance"

    wrong = client.post("/approvals/REQ-1", json={"role": "risk", "decision": "approve"})
    assert wrong.status_code == 400

    ok = client.post("/approvals/REQ-1", json={"role": "finance", "decision": "approve"})
    assert ok.status_code == 200
    assert ok.json()["state"] == "completed"
    assert client.get("/approvals").json()["approvals"] == []


def test_log_endpoint(client: TestClient) -> None:
    entries = client.get("/requests/REQ-1/log").json()["entries"]
    assert entries[0]["event"] == "request_received"
    assert client.get("/requests/REQ-NOPE/log").status_code == 404


def test_operations_and_policy_endpoints(client: TestClient) -> None:
    ops = client.get("/operations").json()["operations"]
    assert {op["name"] for op in ops} == {
        "read_account",
        "apply_credit",
        "apply_debit",
        "freeze_account",
        "unfreeze_account",
        "notify_customer",
    }

    rules = client.get("/policy").json()
    assert len(rules["rules"]) == 6
    assert rules["roles"] == ["finance", "risk", "supervisor"]
