from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import unquote, urlparse

from .core import (
    DEFAULT_DB,
    MATERIALITY,
    POLICY_RULES,
    AppError,
    Engine,
    Store,
)


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "GovernedServiceRunner/0.1"

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        try:
            if path == "/health":
                self._send({"status": "ok"})
            elif path == "/requests":
                self._with_store(lambda store: self._send(store.requests()))
            elif path == "/approvals":
                self._with_store(lambda store: self._send(store.pending_approvals()))
            elif path == "/operations":
                self._send(
                    [
                        {"name": name, "materiality": materiality}
                        for name, materiality in MATERIALITY.items()
                    ]
                )
            elif path == "/policy":
                self._send(POLICY_RULES)
            elif path.startswith("/requests/"):
                parts = path.split("/")
                reference = unquote(parts[2])
                if len(parts) == 4 and parts[3] == "log":
                    self._with_store(
                        lambda store: self._send(store.log(reference))
                    )
                elif len(parts) == 3:
                    self._with_store(
                        lambda store: self._send(store.request_details(reference))
                    )
                else:
                    self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
            else:
                self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except AppError as exc:
            self._send({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        try:
            if not path.startswith("/approvals/"):
                self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            reference = unquote(path.removeprefix("/approvals/"))
            if not reference or "/" in reference:
                self._send({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            content_length = self.headers.get("Content-Length")
            if content_length is None:
                raise AppError("Content-Length is required")
            try:
                length = int(content_length)
            except ValueError as exc:
                raise AppError("invalid Content-Length") from exc
            if length < 0 or length > 1_000_000:
                raise AppError("request body is too large")
            try:
                body: Any = json.loads(self.rfile.read(length))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise AppError("request body must be valid JSON") from exc
            if not isinstance(body, dict):
                raise AppError("request body must be an object")
            role = body.get("role")
            decision = body.get("decision")
            if not isinstance(role, str) or not isinstance(decision, str):
                raise AppError("role and decision are required")
            store = Store(DEFAULT_DB)
            try:
                store.initialize()
                Engine(store).decide(reference, role, decision)
                self._send(store.request_details(reference))
            finally:
                store.close()
        except AppError as exc:
            self._send({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _with_store(self, action: Any) -> None:
        store = Store(DEFAULT_DB)
        try:
            store.initialize()
            action(store)
        finally:
            store.close()

    def _send(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, indent=2, sort_keys=True).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    server = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"Serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
