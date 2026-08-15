from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_request
from fastapi.testclient import TestClient

from app.api import create_app
from app.domain import Account, World
from app.engine import Runner
from app.enums import Materiality, OperationName, RequestKind, Role, Tier
from app.store import Store
from app.values import Money


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "api.sqlite3"
    store = Store(db)
    world = World(
        (Account("ACC-100", "Ada", Tier.STANDARD, Money.parse("1200.00"), False),)
    )
    store.save_world(world)
    runner = Runner(store, world)
    runner.intake(make_request("REQ-1", RequestKind.GOODWILL_CREDIT, "ACC-100", "50.00"))
    runner.intake(make_request("REQ-2", RequestKind.GOODWILL_CREDIT, "ACC-100", "250.00"))
    store.close()
    with TestClient(create_app(db)) as c:
        yield c


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_list_requests(client: TestClient) -> None:
    body = client.get("/requests").json()
    assert [r["reference"] for r in body] == ["REQ-1", "REQ-2"]


def test_get_one_request(client: TestClient) -> None:
    body = client.get("/requests/REQ-2").json()
    assert body["state"] == "awaiting_approval"
    assert len(body["steps"]) == 3


def test_unknown_request_is_404(client: TestClient) -> None:
    assert client.get("/requests/REQ-NOPE").status_code == 404


def test_pending_approvals(client: TestClient) -> None:
    body = client.get("/approvals").json()
    assert len(body) == 1
    assert body[0]["reference"] == "REQ-2"
    assert body[0]["required_role"] == Role.FINANCE.value


def test_resolve_approval(client: TestClient) -> None:
    body = client.post(
        "/approvals/REQ-2", json={"role": "finance", "decision": "approve"}
    ).json()
    assert body["state"] == "completed"
    assert client.get("/approvals").json() == []


def test_resolve_with_wrong_role_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/approvals/REQ-2", json={"role": "risk", "decision": "approve"}
    )
    assert response.status_code == 400
    assert "finance" in response.json()["error"]


def test_log_for_request(client: TestClient) -> None:
    body = client.get("/requests/REQ-1/log").json()
    assert body[0]["kind"] == "request_received"
    assert body[-1]["kind"] == "request_finalised"


def test_operations_listing(client: TestClient) -> None:
    body = client.get("/operations").json()
    assert len(body) == len(OperationName)
    by_name = {o["name"]: o["materiality"] for o in body}
    assert by_name[OperationName.READ_ACCOUNT.value] == Materiality.READ.value
    assert by_name[OperationName.APPLY_DEBIT.value] == Materiality.WRITE.value


def test_policy_listing(client: TestClient) -> None:
    body = client.get("/policy").json()
    assert len(body) == 6
    assert body[0]["required_role"] is None
    assert body[3]["required_role"] == Role.FINANCE.value
