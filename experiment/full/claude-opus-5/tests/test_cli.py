import json
from pathlib import Path

import pytest

from app.cli import main
from app.enums import ExportFormat

FOLDER = Path(__file__).resolve().parent.parent
ACCEPTANCE = """\
REQ-1001  goodwill_credit     completed
REQ-1002  goodwill_credit     completed
REQ-1003  account_recovery    rejected
REQ-1004  collect_debt        failed
ACC-100      1500.00  active
ACC-200        50.00  frozen
"""


@pytest.fixture
def database(tmp_path: Path) -> Path:
    return tmp_path / "run.db"


def run_scenario(database: Path) -> int:
    return main(
        [
            "run",
            str(FOLDER / "scenario.json"),
            "--world",
            str(FOLDER / "world.json"),
            "--database",
            str(database),
        ]
    )


def test_run_matches_the_acceptance_output(capsys, database: Path) -> None:
    assert run_scenario(database) == 0
    assert capsys.readouterr().out == ACCEPTANCE


def test_run_is_repeatable(capsys, database: Path) -> None:
    run_scenario(database)
    first = capsys.readouterr().out
    run_scenario(database)
    assert capsys.readouterr().out == first


def test_show_reports_an_unknown_reference(capsys, database: Path) -> None:
    run_scenario(database)
    capsys.readouterr()

    assert main(["show", "REQ-9999", "--database", str(database)]) == 1
    assert "REQ-9999" in capsys.readouterr().err


def test_show_looks_a_reference_up_once(capsys, database: Path, monkeypatch) -> None:
    run_scenario(database)
    capsys.readouterr()
    lookups: list[str] = []

    import app.cli as cli

    original = cli.request_view

    def counted(store, reference: str):
        lookups.append(reference)
        return original(store, reference)

    monkeypatch.setattr(cli, "request_view", counted)
    assert main(["show", "REQ-1002", "REQ-1002", "--database", str(database)]) == 0
    assert lookups == ["REQ-1002"]
    assert capsys.readouterr().out.count("REQ-1002  goodwill_credit") == 2


def test_missing_world_file_is_reported(capsys, tmp_path: Path) -> None:
    exit_code = main(
        [
            "run",
            str(FOLDER / "scenario.json"),
            "--world",
            str(tmp_path / "absent.json"),
        ]
    )
    assert exit_code == 1
    assert "absent.json" in capsys.readouterr().err


def test_malformed_scenario_is_reported(capsys, tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text(json.dumps({"steps": [{"action": "explode"}]}))

    assert main(["run", str(scenario), "--world", str(FOLDER / "world.json")]) == 1
    assert "malformed" in capsys.readouterr().err


def test_negative_amount_is_reported(capsys, tmp_path: Path) -> None:
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "steps": [
                    {
                        "action": "intake",
                        "request": {
                            "reference": "REQ-1",
                            "kind": "goodwill_credit",
                            "account": "ACC-100",
                            "amount": "-5.00",
                            "requester": {
                                "name": "Nia",
                                "role": "agent",
                                "origin": "internal",
                            },
                        },
                    }
                ]
            }
        )
    )

    assert main(["run", str(scenario), "--world", str(FOLDER / "world.json")]) == 1
    assert "negative" in capsys.readouterr().err


@pytest.mark.parametrize("export_format", list(ExportFormat))
def test_export_writes_every_format(
    capsys, tmp_path: Path, database: Path, export_format: ExportFormat
) -> None:
    run_scenario(database)
    capsys.readouterr()

    assert (
        main(
            [
                "export",
                "--format",
                export_format.value,
                "--into",
                str(tmp_path),
                "--database",
                str(database),
            ]
        )
        == 0
    )
    written = (tmp_path / f"report.{export_format.value}").read_text()
    assert "REQ-1004" in written
    assert "collect_debt" in written
