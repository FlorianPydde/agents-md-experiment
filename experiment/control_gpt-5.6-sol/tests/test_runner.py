from __future__ import annotations

import json
import threading
import urllib.request
from decimal import Decimal
from pathlib import Path

import pytest

from app.api import ApiServer
from app.catalog import policy_for
from app.service import Runner
from app.storage import Store


def account(
    account_id: str = "ACC-1",
    *,
    balance: str = "500.00",
    frozen: bool = False,
) -> dict[str, object]:
    return {
        "id": account_id,
        "owner": "Test Owner",
        "tier": "standard",
        "balance": balance,
        "frozen": frozen,
    }


def request(
    reference: str,
    kind: str,
    *,
    amount: str = "50.00",
    account_id: str = "ACC-1",
) -> dict[str, object]:
    return {
        "reference": reference,
        "kind": kind,
        "account": account_id,
        "amount": amount,
        "requester": {
            "name": "Test Agent",
            "role": "agent",
            "origin": "internal",
        },
    }


@pytest.fixture
def runner(tmp_path: Path):
    store = Store(tmp_path / "runner.db")
    store.initialize()
    service = Runner(store)
    service.seed_accounts([account()])
    yield service
    store.close()


@pytest.mark.parametrize(
    ("operation", "arguments", "rule", "role"),
    [
        ("read_account", {"account": "ACC-1"}, 1, None),
        (
            "apply_credit",
            {"account": "ACC-1", "amount": Decimal("100.00")},
            2,
            None,
        ),
        (
            "apply_credit",
            {"account": "ACC-1", "amount": "100.01"},
            3,
            "finance",
        ),
        ("apply_debit", {"account": "ACC-1", "amount": "1.00"}, 4, "finance"),
        ("freeze_account", {"account": "ACC-1"}, 5, "risk"),
        ("unfreeze_account", {"account": "ACC-1"}, 5, "risk"),
        (
            "notify_customer",
            {"account": "ACC-1", "origin": "internal"},
            6,
            None,
        ),
    ],
)
def test_first_matching_policy_rule(
    operation: str,
    arguments: dict[str, object],
    rule: int,
    role: str | None,
) -> None:
    assert policy_for(operation, arguments) == (rule, role)


def test_approval_flow_resumes_and_completes(runner: Runner) -> None:
    runner.intake(request("REQ-1", "goodwill_credit", amount="250.00"))

    pending = runner.store.list_pending_approvals()
    assert pending == [
        {
            "reference": "REQ-1",
            "step": 1,
            "operation": "apply_credit",
            "required_role": "finance",
        }
    ]
    assert runner.store.request_by_reference("REQ-1")["state"] == "awaiting_approval"

    runner.decide("REQ-1", "finance", "approve")

    detail = runner.store.request_detail("REQ-1")
    assert detail is not None
    assert detail["state"] == "completed"
    assert [step["state"] for step in detail["steps"]] == [
        "completed",
        "completed",
        "completed",
    ]
    assert runner.store.account("ACC-1")["balance"] == "750.00"


def test_frozen_account_causes_operation_failure(tmp_path: Path) -> None:
    store = Store(tmp_path / "runner.db")
    store.initialize()
    runner = Runner(store)
    runner.seed_accounts([account(frozen=True)])

    runner.intake(request("REQ-FAIL", "goodwill_credit"))

    detail = store.request_detail("REQ-FAIL")
    assert detail is not None
    assert detail["state"] == "failed"
    assert detail["steps"][1]["state"] == "failed"
    assert any(entry["event"] == "operation_failed" for entry in detail["log"])
    assert store.account("ACC-1")["balance"] == "500.00"
    store.close()


def test_event_log_rejects_changes(runner: Runner) -> None:
    runner.intake(request("REQ-LOG", "goodwill_credit"))

    with pytest.raises(Exception, match="event log is append only"):
        runner.store.connection.execute("DELETE FROM event_log")


def test_http_api_lists_and_resolves_approvals(tmp_path: Path) -> None:
    database = tmp_path / "runner.db"
    store = Store(database)
    store.initialize()
    runner = Runner(store)
    runner.seed_accounts([account()])
    runner.intake(request("REQ-API", "collect_debt", amount="25.00"))
    store.close()

    server = ApiServer(("127.0.0.1", 0), str(database))
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(f"{base_url}/health") as response:
            assert json.load(response) == {"status": "ok"}
        with urllib.request.urlopen(f"{base_url}/approvals/pending") as response:
            assert json.load(response)[0]["required_role"] == "finance"

        body = json.dumps({"role": "finance", "decision": "approve"}).encode()
        api_request = urllib.request.Request(
            f"{base_url}/approvals/REQ-API",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(api_request) as response:
            assert json.load(response)["state"] == "completed"

        with urllib.request.urlopen(f"{base_url}/requests/REQ-API/log") as response:
            assert json.load(response)[-1] == {
                "sequence": 11,
                "event": "request_finalized",
                "data": {"state": "completed"},
            }
        with urllib.request.urlopen(f"{base_url}/operations") as response:
            assert len(json.load(response)) == 6
        with urllib.request.urlopen(f"{base_url}/policy") as response:
            assert len(json.load(response)) == 6
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
