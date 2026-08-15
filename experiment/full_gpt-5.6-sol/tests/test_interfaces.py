from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.domain import ApprovalRole, RequestKind
from app.engine import Runner
from app.presentation import export_report
from app.storage import Store
from test_runner import make_request

EXPECTED_OUTPUT = (
    "REQ-1001  goodwill_credit     completed\n"
    "REQ-1002  goodwill_credit     completed\n"
    "REQ-1003  account_recovery    rejected\n"
    "REQ-1004  collect_debt        failed\n"
    "ACC-100      1500.00  active\n"
    "ACC-200        50.00  frozen\n"
)


def test_cli_run_matches_acceptance_output_exactly(tmp_path) -> None:
    project = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app",
            "run",
            str(project / "scenario.json"),
            "--world",
            str(project / "world.json"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == EXPECTED_OUTPUT


def test_cli_reports_malformed_input_without_traceback(tmp_path) -> None:
    project = Path(__file__).parents[1]
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"steps":[{"action":"decide"}]}')
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "app",
            "run",
            str(malformed),
            "--world",
            str(project / "world.json"),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr.startswith("error: ")
    assert "Traceback" not in result.stderr


def test_api_exposes_and_resolves_pending_approval(store: Store) -> None:
    Runner(store).intake(
        make_request("REQ-API", RequestKind.GOODWILL_CREDIT, "150.00")
    )
    client = TestClient(create_app(store))

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/approvals").json() == [
        {
            "reference": "REQ-API",
            "operation": "apply_credit",
            "required_role": ApprovalRole.FINANCE.value,
        }
    ]

    response = client.post(
        "/requests/REQ-API/decisions",
        json={"role": "finance", "decision": "approve"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "completed"
    assert client.get("/requests/REQ-API").json()["state"] == "completed"
    assert client.get("/requests/REQ-API/log").json()[-1]["kind"] == (
        "request_finalized"
    )
    assert len(client.get("/operations").json()) == 6
    assert len(client.get("/policy").json()) == 6


def test_api_returns_clear_error_for_wrong_role(store: Store) -> None:
    Runner(store).intake(
        make_request("REQ-WRONG", RequestKind.COLLECT_DEBT, "10.00")
    )
    client = TestClient(create_app(store))

    response = client.post(
        "/requests/REQ-WRONG/decisions",
        json={"role": "risk", "decision": "approve"},
    )

    assert response.status_code == 409
    assert "requires decision from finance" in response.json()["detail"]


def test_export_writes_each_supported_format(
    store: Store, tmp_path, monkeypatch
) -> None:
    Runner(store).intake(
        make_request("REQ-EXPORT", RequestKind.GOODWILL_CREDIT, "10.00")
    )
    monkeypatch.chdir(tmp_path)

    from app.domain import ExportFormat

    paths = [
        export_report(store, export_format)
        for export_format in ExportFormat
    ]

    assert [path.name for path in paths] == [
        "report.csv",
        "report.json",
        "report.tsv",
    ]
    records = json.loads((tmp_path / "report.json").read_text())
    assert records == [
        {
            "reference": "REQ-EXPORT",
            "kind": "goodwill_credit",
            "state": "completed",
            "account": "ACC-1",
            "amount": "10.00",
        }
    ]
