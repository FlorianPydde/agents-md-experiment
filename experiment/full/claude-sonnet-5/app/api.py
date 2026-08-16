"""HTTP API: a thin read/write layer over the storage and engine."""

from __future__ import annotations

import json
import re
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from app.engine import Engine
from app.errors import AppError
from app.models import DecideEntry
from app.operations import OPERATIONS
from app.policy import POLICY_RULES
from app.storage import Storage

REQUEST_PATH = re.compile(r"^/requests/(?P<reference>[^/]+)$")
REQUEST_LOG_PATH = re.compile(r"^/requests/(?P<reference>[^/]+)/log$")
APPROVAL_DECIDE_PATH = re.compile(r"^/approvals/(?P<reference>[^/]+)/decide$")


def _request_to_dict(row) -> dict[str, Any]:
    return {
        "reference": row["reference"],
        "kind": row["kind"],
        "account": row["account"],
        "amount": str(Decimal(row["amount"]).quantize(Decimal("0.01"))),
        "requester": {
            "name": row["requester_name"],
            "role": row["requester_role"],
            "origin": row["requester_origin"],
        },
        "state": row["state"],
    }


def _approval_to_dict(row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "reference": row["request_reference"],
        "step_index": row["step_index"],
        "role_required": row["role_required"],
        "status": row["status"],
        "decided_by_role": row["decided_by_role"],
    }


def _log_entry_to_dict(row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "type": row["type"],
        "reference": row["request_reference"],
        "data": json.loads(row["data"]),
    }


def make_handler(storage: Storage) -> type[BaseHTTPRequestHandler]:
    engine = Engine(storage)

    class Handler(BaseHTTPRequestHandler):
        server_version = "GovernedServiceRequestRunner/1.0"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]

            if path == "/health":
                self._send_json(200, {"status": "ok"})
                return

            if path == "/requests":
                rows = storage.all_requests_in_arrival_order()
                self._send_json(200, [_request_to_dict(row) for row in rows])
                return

            if path == "/approvals":
                rows = storage.all_pending_approvals()
                self._send_json(200, [_approval_to_dict(row) for row in rows])
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
                self._send_json(200, {"rules": POLICY_RULES})
                return

            match = REQUEST_LOG_PATH.match(path)
            if match:
                reference = match.group("reference")
                if storage.get_request(reference) is None:
                    self._send_json(404, {"error": f"no such request: {reference}"})
                    return
                rows = storage.log_for_request(reference)
                self._send_json(200, [_log_entry_to_dict(row) for row in rows])
                return

            match = REQUEST_PATH.match(path)
            if match:
                reference = match.group("reference")
                row = storage.get_request(reference)
                if row is None:
                    self._send_json(404, {"error": f"no such request: {reference}"})
                    return
                self._send_json(200, _request_to_dict(row))
                return

            self._send_json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            match = APPROVAL_DECIDE_PATH.match(path)
            if not match:
                self._send_json(404, {"error": "not found"})
                return

            reference = match.group("reference")
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self._send_json(400, {"error": "invalid JSON body"})
                return

            role = payload.get("role")
            decision = payload.get("decision")
            if not isinstance(role, str) or not isinstance(decision, str):
                self._send_json(400, {"error": "role and decision are required"})
                return

            try:
                entry = DecideEntry.from_dict(
                    {"reference": reference, "role": role, "decision": decision}
                )
                engine.decide(entry)
                storage.commit()
            except AppError as exc:
                self._send_json(400, {"error": str(exc)})
                return

            self._send_json(200, _request_to_dict(storage.get_request(reference)))

    return Handler


def serve(storage: Storage, host: str = "127.0.0.1", port: int = 8000) -> None:
    handler_cls = make_handler(storage)
    server = HTTPServer((host, port), handler_cls)
    print(f"serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
