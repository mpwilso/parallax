"""`parallax ui`: the visual inbox, served on this machine only.

Only the person at the browser can decide (invariant 5):
- listens on 127.0.0.1 only;
- a random token is made at launch and kept in memory, never written to disk. The browser gets
  it in the URL fragment (which browsers never send to the server) and passes it back in a header;
- requests with a wrong token, a foreign Host (DNS rebinding) or a foreign Origin are refused;
- it won't start inside a task's process, so a maker can't reach it.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import views
from .core import ParallaxError, Project, refuse_inside_task
from .inbox import resolve_item
from .rules import RulesEditError

PAGE = Path(__file__).with_name("ui.html")
TOKEN_HEADER = "X-Parallax-Token"


class UI:
    def __init__(self, root: Path, port: int = 0):
        refuse_inside_task(root)
        self.root = Path(root)
        Project(self.root)  # fail early if this isn't a parallax project
        self.token = secrets.token_urlsafe(24)
        self.server = ThreadingHTTPServer(("127.0.0.1", port), _handler(self))
        self.port = self.server.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#{self.token}"

    def project(self) -> Project:
        return Project(self.root)  # fresh each request: picks up policy edits and new ledger lines

    def serve(self, open_browser: bool = True) -> None:
        if open_browser:
            webbrowser.open(self.url)
        self.server.serve_forever()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def _handler(ui: UI):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet: the terminal stays readable
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
                             "connect-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data) -> None:
            self._send(code, json.dumps(data).encode("utf-8"), "application/json; charset=utf-8")

        def _allowed(self, needs_token: bool) -> bool:
            hosts = {f"127.0.0.1:{ui.port}", f"localhost:{ui.port}"}  # the port is known once we're serving
            origins = {f"http://{h}" for h in hosts}
            if self.headers.get("Host") not in hosts:
                self._json(403, {"error": "wrong host"})
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in origins:
                self._json(403, {"error": "wrong origin"})
                return False
            if needs_token and not hmac.compare_digest(self.headers.get(TOKEN_HEADER, ""), ui.token):
                self._json(401, {"error": "open the link printed by `parallax ui`"})
                return False
            return True

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                if self._allowed(needs_token=False):
                    self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
                return
            if not self._allowed(needs_token=True):
                return
            try:
                if path == "/api/version":
                    st = os.stat(ui.project().ledger.path)
                    self._json(200, {"version": f"{st.st_size}-{st.st_mtime_ns}"})
                elif path == "/api/state":
                    p = ui.project()
                    self._json(200, {"project": p.root.name, "inbox": views.inbox_view(p), "tasks": views.tasks_view(p)})
                elif path.startswith("/api/task/"):
                    self._json(200, views.task_view(ui.project(), path.rsplit("/", 1)[1]))
                else:
                    self._json(404, {"error": "not found"})
            except ParallaxError as err:
                self._json(400, {"error": str(err)})

        def do_POST(self):
            if not self._allowed(needs_token=True):
                return
            if self.path != "/api/resolve":
                return self._json(404, {"error": "not found"})
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(415, {"error": "send json"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            ids, reason, approve = body.get("ids") or [], str(body.get("reason") or ""), body.get("approve")
            if not isinstance(ids, list) or not ids or not isinstance(approve, bool):
                return self._json(400, {"error": "say which items, and approve or reject"})
            if not reason.strip():
                return self._json(400, {"error": "every decision needs a reason"})
            p = ui.project()
            results = []
            for item in ids:
                try:
                    out = resolve_item(p, str(item), approve, reason)
                    results.append({"id": item, "ok": True, "changed": out.changed,
                                    "task": out.task["task"] if out.task else None})
                except RulesEditError as err:
                    results.append({"id": item, "ok": False, "error": str(err).split("\n")[0], "snippet": err.snippet})
                except ParallaxError as err:
                    results.append({"id": item, "ok": False, "error": str(err)})
            self._json(200, {"results": results})

    return Handler
