from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import AppError, Decision, Role
from .storage import Repository, money
from .workflow import OPERATIONS, POLICY_RULES, Runner


def request_json(stored) -> dict[str, object]:
    return {
        "reference": stored.request.reference,
        "kind": stored.request.kind.value,
        "state": stored.state.value,
        "account": stored.request.account_id,
        "amount": money(stored.request.amount),
    }


def create_handler(database_path: Path) -> type[BaseHTTPRequestHandler]:
    class ApiHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            try:
                status, body = self._get()
            except AppError as error:
                status, body = HTTPStatus.NOT_FOUND, {"error": str(error)}
            self._respond(status, body)

        def do_POST(self) -> None:
            try:
                status, body = self._post()
            except AppError as error:
                status, body = HTTPStatus.BAD_REQUEST, {"error": str(error)}
            self._respond(status, body)

        def _get(self) -> tuple[HTTPStatus, object]:
            parts = path_parts(self.path)
            if parts == ():
                return HTTPStatus.OK, {"service": "governed-service-runner"}
            if parts == ("health",):
                return HTTPStatus.OK, {"status": "ok"}
            with Repository(database_path) as repository:
                if parts == ("requests",):
                    return HTTPStatus.OK, [
                        request_json(request) for request in repository.list_requests()
                    ]
                if parts == ("approvals",):
                    return HTTPStatus.OK, [
                        {
                            "reference": approval.request_reference,
                            "step_id": approval.step_id,
                            "required_role": approval.required_role.value,
                            "state": approval.state.value,
                        }
                        for approval in repository.list_pending_approvals()
                    ]
                if parts == ("operations",):
                    return HTTPStatus.OK, [
                        {
                            "name": operation.name.value,
                            "materiality": operation.materiality.value,
                        }
                        for operation in OPERATIONS.values()
                    ]
                if parts == ("policy",):
                    return HTTPStatus.OK, {"rules": list(POLICY_RULES)}
                if len(parts) == 2 and parts[0] == "requests":
                    stored = repository.get_request(parts[1])
                    if stored is None:
                        raise AppError(f"request not found: {parts[1]}")
                    return HTTPStatus.OK, request_json(stored)
                if (
                    len(parts) == 3
                    and parts[0] == "requests"
                    and parts[2] == "log"
                ):
                    if repository.get_request(parts[1]) is None:
                        raise AppError(f"request not found: {parts[1]}")
                    return HTTPStatus.OK, [
                        {
                            "sequence": entry.sequence,
                            "event": entry.event,
                            "data": entry.data,
                        }
                        for entry in repository.list_events(parts[1])
                    ]
            raise AppError(f"endpoint not found: {self.path}")

        def _post(self) -> tuple[HTTPStatus, object]:
            parts = path_parts(self.path)
            if len(parts) != 2 or parts[0] != "approvals":
                raise AppError(f"endpoint not found: {self.path}")
            content_length = int(self.headers.get("Content-Length", "0"))
            try:
                payload = json.loads(self.rfile.read(content_length))
            except json.JSONDecodeError as error:
                raise AppError("request body must be valid JSON") from error
            if not isinstance(payload, dict):
                raise AppError("request body must be an object")
            try:
                role = Role(payload["role"])
                decision = Decision(payload["decision"])
            except KeyError as error:
                raise AppError(f"request body is missing '{error.args[0]}'") from error
            except ValueError as error:
                raise AppError("role or decision is invalid") from error
            with Repository(database_path) as repository:
                Runner(repository).decide(parts[1], role, decision)
                stored = repository.get_request(parts[1])
                assert stored is not None
                return HTTPStatus.OK, request_json(stored)

        def _respond(self, status: HTTPStatus, body: object) -> None:
            content = json.dumps(body, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format: str, *args: object) -> None:
            return

    return ApiHandler


def path_parts(path: str) -> tuple[str, ...]:
    return tuple(
        unquote(part)
        for part in urlsplit(path).path.split("/")
        if part
    )


def serve(database_path: Path, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), create_handler(database_path))
    print(f"Serving on http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
