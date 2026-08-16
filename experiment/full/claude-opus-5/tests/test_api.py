from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import build_api
from app.cli import main

FOLDER = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(tmp_path: Path, capsys) -> TestClient:
    database = tmp_path / "api.db"
    main(
        [
            "run",
            str(FOLDER / "scenario.json"),
            "--world",
            str(FOLDER / "world.json"),
            "--database",
            str(database),
        ]
    )
    capsys.readouterr()
    return TestClient(build_api(database))


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_list_and_fetch_requests(client: TestClient) -> None:
    listed = client.get("/requests").json()
    assert [entry["reference"] for entry in listed] == [
        "REQ-1001",
        "REQ-1002",
        "REQ-1003",
        "REQ-1004",
    ]

    detail = client.get("/requests/REQ-1002").json()
    assert detail["request"]["state"] == "completed"
    assert [step["operation"] for step in detail["steps"]] == [
        "read_account",
        "apply_credit",
        "notify_customer",
    ]
    assert detail["approvals"][0]["role"] == "finance"


def test_unknown_reference_is_not_found(client: TestClient) -> None:
    response = client.get("/requests/REQ-9999")
    assert response.status_code == 404
    assert "REQ-9999" in response.json()["error"]


def test_log_entries(client: TestClient) -> None:
    entries = client.get("/requests/REQ-1004/log").json()
    kinds = [entry["kind"] for entry in entries]
    assert kinds[0] == "request_received"
    assert "operation_failed" in kinds
    assert kinds[-1] == "request_finished"


def test_operations_and_policy(client: TestClient) -> None:
    operations = client.get("/operations").json()
    assert len(operations) == 6
    assert {"name": "read_account", "materiality": "read"} in operations

    rules = client.get("/policy").json()
    assert len(rules) == 6
    assert rules[0]["required_role"] is None
    assert rules[3]["required_role"] == "finance"


def test_pending_approvals_and_resolution(client: TestClient, tmp_path: Path) -> None:
    assert client.get("/approvals").json() == []

    posted = client.post(
        "/approvals/REQ-1002", json={"role": "finance", "decision": "approve"}
    )
    assert posted.status_code == 409
    assert "nothing pending" in posted.json()["error"]


def test_resolving_a_live_approval(tmp_path: Path) -> None:
    from decimal import Decimal

    from app.domain import Account, Money, Requester, ServiceRequest
    from app.engine import intake
    from app.enums import Origin, RequestKind, Tier
    from app.store import Store

    database = tmp_path / "live.db"
    store = Store(database)
    store.start_clean(
        (
            Account(
                id="ACC-100",
                owner="Ada Lovelace",
                tier=Tier.STANDARD,
                balance=Money(Decimal("1200.00")),
                frozen=False,
            ),
        )
    )
    intake(
        store,
        ServiceRequest(
            reference="REQ-77",
            kind=RequestKind.GOODWILL_CREDIT,
            account="ACC-100",
            amount=Money(Decimal("250.00")),
            requester=Requester(name="Nia", role="agent", origin=Origin.INTERNAL),
        ),
    )
    store.close()

    client = TestClient(build_api(database))
    pending = client.get("/approvals").json()
    assert pending == [
        {"reference": "REQ-77", "position": 1, "role": "finance", "state": "pending"}
    ]

    refused = client.post(
        "/approvals/REQ-77", json={"role": "risk", "decision": "approve"}
    )
    assert refused.status_code == 409

    resolved = client.post(
        "/approvals/REQ-77", json={"role": "finance", "decision": "approve"}
    )
    assert resolved.status_code == 200
    assert resolved.json()["state"] == "completed"
    assert client.get("/approvals").json() == []
