from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import AppError
from .service import Runner
from .storage import Store


class ApiServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], database: str):
        self.database = database
        super().__init__(address, ApiHandler)


class ApiHandler(BaseHTTPRequestHandler):
    server: ApiServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        store = Store(self.server.database)
        try:
            if path == "/health":
                self._send(HTTPStatus.OK, {"status": "ok"})
            elif path == "/requests":
                self._send(HTTPStatus.OK, store.list_requests())
            elif path == "/approvals/pending":
                self._send(HTTPStatus.OK, store.list_pending_approvals())
            elif path == "/operations":
                self._send(HTTPStatus.OK, Runner.operations())
            elif path == "/policy":
                self._send(HTTPStatus.OK, Runner.policy_rules())
            elif path.startswith("/requests/") and path.endswith("/log"):
                reference = unquote(path[len("/requests/") : -len("/log")]).rstrip("/")
                request = store.request_by_reference(reference)
                if request is None:
                    self._send(
                        HTTPStatus.NOT_FOUND,
                        {"error": f"request not found: {reference}"},
                    )
                else:
                    self._send(
                        HTTPStatus.OK, store.log_for_request(request["id"])
                    )
            elif path.startswith("/requests/"):
                reference = unquote(path[len("/requests/") :])
                detail = store.request_detail(reference)
                if detail is None:
                    self._send(
                        HTTPStatus.NOT_FOUND,
                        {"error": f"request not found: {reference}"},
                    )
                else:
                    self._send(HTTPStatus.OK, detail)
            else:
                self._send(HTTPStatus.NOT_FOUND, {"error": "endpoint not found"})
        finally:
            store.close()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if not path.startswith("/approvals/"):
            self._send(HTTPStatus.NOT_FOUND, {"error": "endpoint not found"})
            return
        reference = unquote(path[len("/approvals/") :])
        try:
            body = self._read_json()
        except AppError as error:
            self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        store = Store(self.server.database)
        try:
            runner = Runner(store)
            try:
                runner.decide(reference, body.get("role"), body.get("decision"))
            except AppError as error:
                self._send(HTTPStatus.BAD_REQUEST, {"error": str(error)})
                return
            self._send(HTTPStatus.OK, store.request_detail(reference))
        finally:
            store.close()

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise AppError("invalid Content-Length") from error
        try:
            value = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise AppError("request body must be valid JSON") from error
        if not isinstance(value, dict):
            raise AppError("request body must be a JSON object")
        return value

    def _send(self, status: HTTPStatus, value: Any) -> None:
        body = (json.dumps(value, separators=(",", ":")) + "\n").encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
