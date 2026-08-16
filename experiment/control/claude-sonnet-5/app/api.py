"""``serve``: a small HTTP API over the current database state.

Endpoints:

- ``GET  /health`` - liveness check.
- ``GET  /requests`` - list all requests.
- ``GET  /requests/<reference>`` - fetch one request.
- ``GET  /requests/<reference>/log`` - the log entries for one request.
- ``GET  /approvals`` - list pending approvals.
- ``POST /approvals/<reference>`` - resolve a pending approval; body is
  ``{"role": "...", "decision": "approve"|"reject"}``.
- ``GET  /operations`` - the supported operations and their materiality.
- ``GET  /policy`` - the policy rules, in match order.
"""

from __future__ import annotations

import json
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer

from app import engine
from app.errors import AppError
from app.models import MATERIALITY, OPERATIONS
from app.policy import policy_rules
from app.storage import connect

REQUEST_PATH = re.compile(r"^/requests/(?P<reference>[^/]+)$")
REQUEST_LOG_PATH = re.compile(r"^/requests/(?P<reference>[^/]+)/log$")
APPROVAL_PATH = re.compile(r"^/approvals/(?P<reference>[^/]+)$")


def _request_to_dict(row: sqlite3.Row) -> dict:
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
        "current_step": row["current_step"],
    }


def _approval_to_dict(row: sqlite3.Row) -> dict:
    return {
        "request_ref": row["request_ref"],
        "step": row["step_idx"],
        "role": row["role"],
        "status": row["status"],
    }


def make_handler(db_path: str) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "GovernedServiceRunner/0.1"

        def _connect(self) -> sqlite3.Connection:
            return connect(db_path)

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, indent=2, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            pass  # keep test output quiet; behaviour is unaffected

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            conn = self._connect()
            try:
                if path == "/health":
                    self._send_json(200, {"status": "ok"})
                    return

                if path == "/requests":
                    rows = conn.execute(
                        "SELECT * FROM requests ORDER BY seq"
                    ).fetchall()
                    self._send_json(200, [_request_to_dict(r) for r in rows])
                    return

                if path == "/approvals":
                    rows = conn.execute(
                        "SELECT * FROM approvals WHERE status = 'pending' ORDER BY id"
                    ).fetchall()
                    self._send_json(200, [_approval_to_dict(r) for r in rows])
                    return

                if path == "/operations":
                    self._send_json(
                        200,
                        [{"name": name, "materiality": MATERIALITY[name]} for name in OPERATIONS],
                    )
                    return

                if path == "/policy":
                    self._send_json(200, policy_rules())
                    return

                match = REQUEST_LOG_PATH.match(path)
                if match:
                    reference = match.group("reference")
                    exists = conn.execute(
                        "SELECT 1 FROM requests WHERE reference = ?", (reference,)
                    ).fetchone()
                    if exists is None:
                        self._send_json(404, {"error": f"unknown request reference: {reference}"})
                        return
                    rows = conn.execute(
                        "SELECT seq, event_type, data FROM log"
                        " WHERE request_ref = ? ORDER BY seq",
                        (reference,),
                    ).fetchall()
                    self._send_json(
                        200,
                        [
                            {
                                "seq": r["seq"],
                                "event_type": r["event_type"],
                                "data": json.loads(r["data"]),
                            }
                            for r in rows
                        ],
                    )
                    return

                match = REQUEST_PATH.match(path)
                if match:
                    reference = match.group("reference")
                    row = conn.execute(
                        "SELECT * FROM requests WHERE reference = ?", (reference,)
                    ).fetchone()
                    if row is None:
                        self._send_json(404, {"error": f"unknown request reference: {reference}"})
                        return
                    self._send_json(200, _request_to_dict(row))
                    return

                self._send_json(404, {"error": "not found"})
            finally:
                conn.close()

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            match = APPROVAL_PATH.match(path)
            if not match:
                self._send_json(404, {"error": "not found"})
                return

            reference = match.group("reference")
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw_body or b"{}")
            except json.JSONDecodeError:
                self._send_json(400, {"error": "body must be JSON"})
                return

            role = body.get("role")
            decision = body.get("decision")
            if not role or not decision:
                self._send_json(400, {"error": "body must include 'role' and 'decision'"})
                return

            conn = self._connect()
            try:
                engine.decide(conn, reference, role, decision)
                conn.commit()
                row = conn.execute(
                    "SELECT * FROM requests WHERE reference = ?", (reference,)
                ).fetchone()
            except AppError as exc:
                self._send_json(400, {"error": str(exc)})
                return
            finally:
                conn.close()

            self._send_json(200, _request_to_dict(row))

    return Handler


def serve(db_path: str, host: str = "127.0.0.1", port: int = 8000) -> None:
    handler_cls = make_handler(db_path)
    httpd = HTTPServer((host, port), handler_cls)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
