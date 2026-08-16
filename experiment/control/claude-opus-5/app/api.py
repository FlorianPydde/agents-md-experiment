"""A small HTTP API over the same store the commands use.

The server is single threaded on purpose: one SQLite connection, one request at
a time, no locking to reason about.  Routes are held in a table so that the
handler stays short.
"""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable

from . import export, operations, policy
from .engine import Engine
from .errors import AppError
from .models import Decision, Role
from .reporting import Lookup
from .store import Store

Handler = Callable[..., Any]


class Api:
    """The routing table and the handlers behind it."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.lookup = Lookup(store)
        self.routes: list[tuple[str, re.Pattern[str], Handler]] = [
            ("GET", re.compile(r"^/health$"), self.health),
            ("GET", re.compile(r"^/requests$"), self.requests),
            ("GET", re.compile(r"^/requests/([^/]+)$"), self.request),
            ("GET", re.compile(r"^/requests/([^/]+)/log$"), self.log),
            ("GET", re.compile(r"^/approvals$"), self.approvals),
            ("POST", re.compile(r"^/approvals/([^/]+)$"), self.decide),
            ("GET", re.compile(r"^/operations$"), self.operations),
            ("GET", re.compile(r"^/policy$"), self.policy),
        ]

    def dispatch(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, Any]:
        for verb, pattern, handler in self.routes:
            match = pattern.match(path)
            if not match:
                continue
            if verb != method:
                return 405, {"error": f"{method} is not allowed on {path}"}
            try:
                if verb == "POST":
                    return handler(*match.groups(), body=body)
                return handler(*match.groups())
            except AppError as error:
                return 400, {"error": str(error)}
        return 404, {"error": f"no such resource: {path}"}

    # -- handlers ------------------------------------------------------
    def health(self) -> tuple[int, Any]:
        return 200, {"status": "ok", "requests": len(self.store.requests())}

    def requests(self) -> tuple[int, Any]:
        return 200, {"requests": export.records(self.store)}

    def request(self, reference: str) -> tuple[int, Any]:
        try:
            return 200, self.lookup.fetch(reference)
        except AppError as error:
            return 404, {"error": str(error)}

    def log(self, reference: str) -> tuple[int, Any]:
        if self.store.request(reference) is None:
            return 404, {"error": f"no such request '{reference}'"}
        return 200, {"reference": reference, "log": self.store.events(reference)}

    def approvals(self) -> tuple[int, Any]:
        return 200, {
            "pending": [
                {
                    "reference": approval.reference,
                    "step": approval.step_index,
                    "operation": approval.operation,
                    "role": str(approval.role),
                }
                for approval in self.store.pending_approvals()
            ]
        }

    def decide(self, reference: str, body: dict[str, Any]) -> tuple[int, Any]:
        if self.store.request(reference) is None:
            return 404, {"error": f"no such request '{reference}'"}
        try:
            role = Role(str(body.get("role")))
        except ValueError:
            return 400, {"error": "field 'role' must be one of: finance, risk, supervisor"}
        try:
            decision = Decision(str(body.get("decision")))
        except ValueError:
            return 400, {"error": "field 'decision' must be one of: approve, reject"}
        state = Engine(self.store).decide(reference, role, decision)
        self.lookup.forget()
        return 200, {"reference": reference, "state": str(state)}

    def operations(self) -> tuple[int, Any]:
        return 200, {"operations": operations.catalogue()}

    def policy(self) -> tuple[int, Any]:
        return 200, {"rules": policy.catalogue()}


def make_handler(api: Api) -> type[BaseHTTPRequestHandler]:
    class RequestHandler(BaseHTTPRequestHandler):
        server_version = "governed-runner"
        sys_version = ""

        def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
            self._respond(*api.dispatch("GET", self.path.split("?")[0], {}))

        def do_POST(self) -> None:  # noqa: N802 - name fixed by the base class
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._respond(400, {"error": "the body is not valid JSON"})
                return
            if not isinstance(body, dict):
                self._respond(400, {"error": "the body must be a JSON object"})
                return
            self._respond(*api.dispatch("POST", self.path.split("?")[0], body))

        def log_message(self, *args: Any) -> None:
            """Silence the default logging: it carries a timestamp."""

        def _respond(self, status: int, payload: Any) -> None:
            body = (json.dumps(payload, indent=2) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return RequestHandler


def serve(store: Store, host: str, port: int) -> None:
    server = HTTPServer((host, port), make_handler(Api(store)))
    print(f"listening on http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    finally:
        server.server_close()
