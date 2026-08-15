"""`serve` command: a minimal HTTP API over the persistent store.

Implemented on the standard library's http.server to avoid third-party
dependencies. Routes:

  GET  /health
  GET  /requests
  GET  /requests/<reference>
  GET  /approvals/pending
  POST /approvals/<reference>/resolve   body: {"role": ..., "decision": ...}
  GET  /requests/<reference>/log
  GET  /operations
  GET  /policy
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from app.engine import Engine
from app.errors import AppError
from app.loaders import DecideStep
from app.operations import OPERATIONS
from app.policy import RULES
from app.store import Store


def _request_to_dict(row) -> dict:
    return {
        "reference": row["reference"],
        "kind": row["kind"],
        "account": row["account"],
        "amount": row["amount"],
        "requester": {
            "name": row["requester_name"],
            "role": row["requester_role"],
            "origin": row["requester_origin"],
        },
        "state": row["state"],
        "next_step_index": row["next_step_index"],
    }


def _approval_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "reference": row["reference"],
        "step_index": row["step_index"],
        "role": row["role"],
        "state": row["state"],
    }


def make_handler(db_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - silence stdlib logging
            pass

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _store(self) -> Store:
            return Store.open_existing(db_path)

        def do_GET(self):  # noqa: N802 - required by BaseHTTPRequestHandler
            path = self.path.split("?", 1)[0]

            if path == "/health":
                self._send_json(200, {"status": "ok"})
                return

            if path == "/operations":
                self._send_json(
                    200,
                    [
                        {"name": op.name, "materiality": op.materiality}
                        for op in OPERATIONS.values()
                    ],
                )
                return

            if path == "/policy":
                self._send_json(200, RULES)
                return

            if path == "/requests":
                store = self._store()
                try:
                    self._send_json(
                        200, [_request_to_dict(r) for r in store.list_requests()]
                    )
                finally:
                    store.close()
                return

            match = re.fullmatch(r"/requests/([^/]+)", path)
            if match:
                reference = match.group(1)
                store = self._store()
                try:
                    row = store.get_request(reference)
                    if row is None:
                        self._send_json(404, {"error": f"no such request: {reference}"})
                        return
                    self._send_json(200, _request_to_dict(row))
                finally:
                    store.close()
                return

            match = re.fullmatch(r"/requests/([^/]+)/log", path)
            if match:
                reference = match.group(1)
                store = self._store()
                try:
                    row = store.get_request(reference)
                    if row is None:
                        self._send_json(404, {"error": f"no such request: {reference}"})
                        return
                    self._send_json(200, store.list_log(reference))
                finally:
                    store.close()
                return

            if path == "/approvals/pending":
                store = self._store()
                try:
                    self._send_json(
                        200, [_approval_to_dict(a) for a in store.list_pending_approvals()]
                    )
                finally:
                    store.close()
                return

            self._send_json(404, {"error": f"no such route: {path}"})

        def do_POST(self):  # noqa: N802
            path = self.path.split("?", 1)[0]
            match = re.fullmatch(r"/approvals/([^/]+)/resolve", path)
            if match:
                reference = match.group(1)
                length = int(self.headers.get("Content-Length", 0))
                raw_body = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw_body or b"{}")
                except json.JSONDecodeError:
                    self._send_json(400, {"error": "malformed JSON body"})
                    return

                role = body.get("role")
                decision = body.get("decision")
                if not role or decision not in ("approve", "reject"):
                    self._send_json(
                        400, {"error": "body must include 'role' and decision approve/reject"}
                    )
                    return

                store = self._store()
                try:
                    engine = Engine(store)
                    try:
                        engine.decide(DecideStep(reference=reference, role=role, decision=decision))
                    except AppError as exc:
                        self._send_json(400, {"error": str(exc)})
                        return
                    row = store.get_request(reference)
                    self._send_json(200, _request_to_dict(row))
                finally:
                    store.close()
                return

            self._send_json(404, {"error": f"no such route: {path}"})

    return Handler


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="app serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    return parser


def run(argv: list[str], *, db_path: Path) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not db_path.exists():
        raise AppError(f"no database found at {db_path}; run 'run' first")

    handler = make_handler(db_path)
    server = HTTPServer((args.host, args.port), handler)
    print(f"serving on http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
