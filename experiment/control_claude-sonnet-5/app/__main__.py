"""CLI entry point: `python -m app <command> ...`"""

from __future__ import annotations

import sys
from pathlib import Path

from app.errors import AppError
from app.commands import export as export_cmd
from app.commands import run as run_cmd
from app.commands import serve as serve_cmd
from app.commands import show as show_cmd

DEFAULT_DB_PATH = Path("db.sqlite3")


def _print_error_and_exit(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        _print_error_and_exit("no command given (expected: run, show, export, serve)")

    command, rest = argv[0], argv[1:]
    try:
        if command == "run":
            run_cmd.run(rest, db_path=DEFAULT_DB_PATH)
        elif command == "show":
            show_cmd.run(rest, db_path=DEFAULT_DB_PATH)
        elif command == "export":
            export_cmd.run(rest, db_path=DEFAULT_DB_PATH)
        elif command == "serve":
            serve_cmd.run(rest, db_path=DEFAULT_DB_PATH)
        else:
            _print_error_and_exit(f"unknown command: {command!r}")
    except AppError as exc:
        _print_error_and_exit(str(exc))


if __name__ == "__main__":
    main()
