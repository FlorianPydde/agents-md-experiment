import sqlite3

import pytest

from app import engine
from app.errors import AppError
from app.storage import connect, load_world

WORLD = {
    "accounts": [
        {"id": "ACC-100", "owner": "Ada Lovelace", "tier": "standard", "balance": "1200.00", "frozen": False},
        {"id": "ACC-200", "owner": "Grace Hopper", "tier": "premium", "balance": "50.00", "frozen": True},
    ]
}


@pytest.fixture
def conn():
    connection = connect(":memory:")
    load_world(connection, WORLD)
    yield connection
    connection.close()


def make_request(**overrides):
    request = {
        "reference": "REQ-1",
        "kind": "goodwill_credit",
        "account": "ACC-100",
        "amount": "50.00",
        "requester": {"name": "Nia Patel", "role": "agent", "origin": "internal"},
    }
    request.update(overrides)
    return request


def account_balance(conn: sqlite3.Connection, account_id: str) -> str:
    row = conn.execute("SELECT balance FROM accounts WHERE id = ?", (account_id,)).fetchone()
    return row["balance"]


def request_state(conn: sqlite3.Connection, reference: str) -> str:
    row = conn.execute("SELECT state FROM requests WHERE reference = ?", (reference,)).fetchone()
    return row["state"]


def test_small_credit_completes_without_approval(conn):
    engine.intake(conn, make_request(amount="50.00"))
    assert request_state(conn, "REQ-1") == "completed"
    assert account_balance(conn, "ACC-100") == "1250.00"


def test_large_credit_waits_for_finance_approval(conn):
    engine.intake(conn, make_request(amount="250.00"))
    assert request_state(conn, "REQ-1") == "awaiting_approval"
    pending = conn.execute(
        "SELECT role FROM approvals WHERE request_ref = ? AND status = 'pending'",
        ("REQ-1",),
    ).fetchone()
    assert pending["role"] == "finance"

    engine.decide(conn, "REQ-1", "finance", "approve")
    assert request_state(conn, "REQ-1") == "completed"
    assert account_balance(conn, "ACC-100") == "1450.00"


def test_rejecting_an_approval_stops_the_request(conn):
    engine.intake(
        conn,
        make_request(
            reference="REQ-2",
            kind="account_recovery",
            account="ACC-200",
            amount="75.00",
            requester={"name": "Omar Haddad", "role": "agent", "origin": "external"},
        ),
    )
    assert request_state(conn, "REQ-2") == "awaiting_approval"

    engine.decide(conn, "REQ-2", "risk", "reject")
    assert request_state(conn, "REQ-2") == "rejected"

    steps = conn.execute(
        "SELECT idx, state FROM steps WHERE request_ref = ? ORDER BY idx", ("REQ-2",)
    ).fetchall()
    # unfreeze_account (the pending step) and everything after it were skipped.
    assert [dict(s) for s in steps][1:] == [
        {"idx": 1, "state": "skipped"},
        {"idx": 2, "state": "skipped"},
        {"idx": 3, "state": "skipped"},
    ]
    # The account was never unfrozen.
    frozen = conn.execute("SELECT frozen FROM accounts WHERE id = ?", ("ACC-200",)).fetchone()
    assert frozen["frozen"] == 1


def test_decide_requires_the_correct_role(conn):
    engine.intake(conn, make_request(amount="250.00"))
    with pytest.raises(AppError):
        engine.decide(conn, "REQ-1", "risk", "approve")


def test_decide_on_request_with_nothing_pending_raises(conn):
    engine.intake(conn, make_request(amount="50.00"))
    assert request_state(conn, "REQ-1") == "completed"
    with pytest.raises(AppError):
        engine.decide(conn, "REQ-1", "finance", "approve")


def test_failing_operation_marks_request_failed_and_skips_remaining_steps(conn):
    # ACC-200 starts frozen; collect_debt's apply_debit will fail once approved.
    engine.intake(
        conn,
        make_request(
            reference="REQ-3",
            kind="collect_debt",
            account="ACC-200",
            amount="10.00",
            requester={"name": "Omar Haddad", "role": "agent", "origin": "external"},
        ),
    )
    assert request_state(conn, "REQ-3") == "awaiting_approval"
    engine.decide(conn, "REQ-3", "finance", "approve")

    assert request_state(conn, "REQ-3") == "failed"
    steps = conn.execute(
        "SELECT idx, state FROM steps WHERE request_ref = ? ORDER BY idx", ("REQ-3",)
    ).fetchall()
    assert [dict(s) for s in steps] == [
        {"idx": 0, "state": "done"},
        {"idx": 1, "state": "failed"},
        {"idx": 2, "state": "skipped"},
    ]
    # Balance is unchanged because the debit never applied.
    assert account_balance(conn, "ACC-200") == "50.00"


def test_debit_fails_on_insufficient_balance():
    connection = connect(":memory:")
    load_world(
        connection,
        {
            "accounts": [
                {"id": "ACC-1", "owner": "A", "tier": "standard", "balance": "5.00", "frozen": False},
            ]
        },
    )
    engine.intake(
        connection,
        make_request(
            reference="REQ-4",
            kind="collect_debt",
            account="ACC-1",
            amount="500.00",
            requester={"name": "Omar Haddad", "role": "agent", "origin": "external"},
        ),
    )
    engine.decide(connection, "REQ-4", "finance", "approve")
    assert request_state(connection, "REQ-4") == "failed"
    connection.close()


def test_unknown_kind_is_rejected(conn):
    with pytest.raises(AppError):
        engine.intake(conn, make_request(kind="not_a_real_kind"))


def test_negative_amount_is_rejected(conn):
    with pytest.raises(AppError):
        engine.intake(conn, make_request(amount="-5.00"))


def test_unknown_account_is_rejected(conn):
    with pytest.raises(AppError):
        engine.intake(conn, make_request(account="ACC-999"))
