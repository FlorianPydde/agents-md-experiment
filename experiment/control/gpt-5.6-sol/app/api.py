from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import AppError
from .service import OPERATIONS, POLICY_RULES, Service


MAX_BODY_SIZE = 64 * 1024


class ApiHandler(BaseHTTPRequestHandler):
    service: Service

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send(self, status: int, body: Any) -> None:
        encoded = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _reference_route(self, path: str) -> tuple[str, str] | None:
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "requests":
            return unquote(parts[1]), parts[2] if len(parts) == 3 else ""
        return None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._send(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/requests":
            self._send(HTTPStatus.OK, self.service.requests())
            return
        if path == "/approvals":
            self._send(HTTPStatus.OK, self.service.pending_approvals())
            return
        if path == "/operations":
            self._send(
                HTTPStatus.OK,
                [
                    {"name": name, "materiality": materiality}
                    for name, materiality in OPERATIONS.items()
                ],
            )
            return
        if path == "/policy":
            self._send(HTTPStatus.OK, POLICY_RULES)
            return
        route = self._reference_route(path)
        if route:
            reference, suffix = route
            detail = self.service.db.request_detail(reference)
            if detail is None:
                self._send(HTTPStatus.NOT_FOUND, {"error": f"request not found: {reference}"})
            elif suffix == "":
                self._send(HTTPStatus.OK, detail)
            elif suffix == "log":
                self._send(HTTPStatus.OK, self.service.db.log_entries(reference))
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "endpoint not found"})
            return
        self._send(HTTPStatus.NOT_FOUND, {"error": "endpoint not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        route = self._reference_route(path)
        if not route or route[1] != "decisions":
            self._send(HTTPStatus.NOT_FOUND, {"error": "endpoint not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > MAX_BODY_SIZE:
                self._send(
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                    {"error": f"request body must not exceed {MAX_BODY_SIZE} bytes"},
                )
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise AppError("request body must be an object")
            role = body.get("role")
            decision = body.get("decision")
            if not isinstance(role, str):
                raise AppError("role must be a string")
            if not isinstance(decision, str):
                raise AppError("decision must be a string")
            self.service.decide(route[0], role, decision)
        except (json.JSONDecodeError, ValueError):
            self._send(HTTPStatus.BAD_REQUEST, {"error": "malformed JSON request body"})
            return
        except AppError as exc:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send(HTTPStatus.OK, self.service.db.request_detail(route[0]))


def serve(service: Service, host: str, port: int) -> None:
    handler = type("ConfiguredApiHandler", (ApiHandler,), {"service": service})
    server = HTTPServer((host, port), handler)
    print(f"Serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
