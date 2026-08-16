from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.engine import Engine


@pytest.fixture
def client(replayed: Engine, database: Path) -> TestClient:
    replayed.store.close()
    return TestClient(create_app(database))


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_and_fetch_requests(client: TestClient) -> None:
    listed = client.get("/requests").json()
    one = client.get("/requests/REQ-1002").json()

    assert [item["reference"] for item in listed] == [
        "REQ-1001",
        "REQ-1002",
        "REQ-1003",
        "REQ-1004",
    ]
    assert one["state"] == "completed"
    assert one["steps"][1]["required_role"] == "finance"


def test_an_unknown_reference_is_a_404(client: TestClient) -> None:
    response = client.get("/requests/REQ-9999")

    assert response.status_code == 404
    assert "REQ-9999" in response.json()["detail"]


def test_log_for_a_request(client: TestClient) -> None:
    events = client.get("/requests/REQ-1004/log").json()

    assert events[0]["type"] == "request_received"
    assert events[-1]["type"] == "request_finished"


def test_operations_and_policy_are_published(client: TestClient) -> None:
    operations = client.get("/operations").json()
    rules = client.get("/policy").json()

    assert len(operations) == 6
    assert {"name": "read_account", "materiality": "read"}.items() <= operations[0].items()
    assert [rule["name"] for rule in rules][0] == "read_runs_alone"


def test_pending_approvals_can_be_resolved(client: TestClient, database: Path) -> None:
    intake = {
        "reference": "REQ-2001",
        "kind": "goodwill_credit",
        "account": "ACC-100",
        "amount": "250.00",
        "requester": {"name": "Nia Patel", "role": "agent", "origin": "internal"},
    }
    from decimal import Decimal

    from app.domain import Origin, Requester, RequestKind, ServiceRequest
    from app.operations import Ledger
    from app.store import Store

    with Store(database) as store:
        engine = Engine(store, Ledger.from_accounts(store.accounts()))
        engine.intake(
            ServiceRequest(
                reference=intake["reference"],
                kind=RequestKind.GOODWILL_CREDIT,
                account="ACC-100",
                amount=Decimal("250.00"),
                requester=Requester("Nia Patel", "agent", Origin.INTERNAL),
            )
        )

    pending = client.get("/approvals").json()
    assert [item["reference"] for item in pending] == ["REQ-2001"]

    refused = client.post("/approvals/REQ-2001", json={"role": "risk", "decision": "approve"})
    assert refused.status_code == 400

    accepted = client.post("/approvals/REQ-2001", json={"role": "finance", "decision": "approve"})
    assert accepted.status_code == 200
    assert accepted.json()["state"] == "completed"
    assert client.get("/approvals").json() == []
