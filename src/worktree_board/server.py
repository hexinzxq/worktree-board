"""A tiny read-only HTTP surface: one JSON endpoint and one page.

stdlib only (`http.server`), because the value of this tool is that it starts
in a checkout with nothing installed. Bind to localhost by default: the report
contains absolute paths.
"""
from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import threading
import time

from .scan import report

STATIC = Path(__file__).parent / "static"
INDEX = STATIC / "index.html"


def make_handler(root: Path, ttl: float = 2.0):
    """Bind a request handler to one repository root.

    Scans are shared for `ttl` seconds: the page polls every 5s, and a second
    tab must not make the machine run `git status` twice for the same answer.
    Concurrent requests wait for the one scan instead of starting their own.
    """
    cache: dict = {"at": float("-inf"), "value": None}
    lock = threading.Lock()

    def state() -> dict:
        if ttl <= 0:
            return report(root)
        with lock:  # also serialises the scan, so two tabs scan once
            now = time.monotonic()
            if cache["value"] is None or now - cache["at"] >= ttl:
                cache["value"] = report(root)
                cache["at"] = now
            return cache["value"]

    class Handler(BaseHTTPRequestHandler):
        server_version = "worktree-board/0.1"

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode()
            self._send(status, body, "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                try:
                    page = INDEX.read_bytes()
                except OSError:  # a wheel that shipped without static/ is a real install
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {
                        "error": f"missing {INDEX} — the installed package has no page; "
                                 f"reinstall worktree-board"})
                else:
                    self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
            elif path == "/api/state":
                try:
                    self._json(HTTPStatus.OK, state())
                except Exception as error:  # a broken checkout must not kill the server
                    self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(error)})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def log_message(self, fmt: str, *args) -> None:  # quieter than the default
            return

    return Handler


def serve(root: Path, host: str = "127.0.0.1", port: int = 8765, ttl: float = 2.0) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((host, port), make_handler(root, ttl))
    return httpd
