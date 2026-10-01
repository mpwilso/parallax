"""`parallax ui`: the main way to use Parallax. An intake box, a queue, and a decision card.

Only the person at the browser can decide (invariant 5):
- it listens on 127.0.0.1 only;
- a random token, kept with the project's port in a private file beside the approval key (which
  the sandbox can't read), so your bookmark keeps working across restarts; `parallax ui
  --new-token` replaces it. The browser gets it in the URL fragment (which browsers never send to
  the server) and passes it back in a header;
- requests with a wrong token, a foreign Host (DNS rebinding) or a foreign Origin are refused;
- a strict content security policy: scripts and styles only from this server's own files, no
  inline code, no frames. Agent-written text is only ever set as text, never as HTML;
- it won't start inside a task's process, and the sandbox has no route to it.
Every action goes through the same functions as the CLI, so it's recorded the same way.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sys
import traceback
import stat
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import brand, views
from .core import ParallaxError, Project, refuse_inside_task

WEB = Path(__file__).with_name("web")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/app.css": ("app.css", "text/css; charset=utf-8")}
COMPUTED = {"/brand.json": (lambda: brand.bundle_json(), "application/json; charset=utf-8"),  # the logo and portraits, rendered once here
            "/favicon.svg": (lambda: brand.logo_svg().encode("utf-8"), "image/svg+xml")}
TOKEN_HEADER = "X-Parallax-Token"
MAX_BODY = 1_000_000  # a request body is a few words of work or a reason, never more
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' blob:; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def link_path(root: Path) -> Path:
    from .approvals import key_path
    root = Path(root).resolve()
    return key_path().parent / "ui" / f"{root.name}-{hashlib.sha256(str(root).encode()).hexdigest()[:8]}.json"


def _saved_link(root: Path) -> dict:
    path = link_path(root)
    try:
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            return {}  # someone else could have read it: start over
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_link(root: Path, port: int, token: str) -> None:
    path = link_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"port": port, "token": token}, f)


def home_port(root: Path) -> int:
    """This project's own port, the same every time, so a bookmark finds it."""
    return 20000 + int(hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:8], 16) % 20000


class UI:
    def __init__(self, root: Path, port: int | None = None, new_token: bool = False):
        refuse_inside_task(root)
        self.root = Path(root)
        Project(self.root)  # fail early if this isn't a parallax project
        saved = {} if new_token else _saved_link(self.root)
        token = saved.get("token")
        self.token = token if isinstance(token, str) and len(token) >= 24 else secrets.token_urlsafe(24)
        want = port if port is not None else int(saved.get("port") or home_port(self.root))
        try:
            self.server = ThreadingHTTPServer(("127.0.0.1", want), _handler(self))
        except OSError:  # taken, likely by another program: any free port, and the link says which
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self.port = self.server.server_address[1]
        _save_link(self.root, self.port, self.token)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/#{self.token}"

    @property
    def windows_url(self) -> str:
        """The same page from a Windows browser: WSL forwards localhost."""
        return f"http://localhost:{self.port}/#{self.token}"

    def project(self) -> Project:
        return Project(self.root)  # fresh each request: picks up new ledger lines

    def serve(self, open_browser: bool = True) -> None:
        if open_browser:
            webbrowser.open(self.url)
        self.server.serve_forever()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def act(project: Project, path: str, body: dict) -> dict:
    """The actions, shared with the CLI's functions. Returns {"message": ...} or raises ParallaxError."""
    from . import decide, pilot
    from .accept import accept, merge_command
    task = str(body.get("task") or "")
    reason = str(body.get("reason") or "")
    if path == "/api/do":
        work = str(body.get("work") or "").strip()
        if not work:
            raise ParallaxError("describe the work first")
        t = pilot.intake(project, work)
        return {"task": t["task"], "message": f"task {t['task']}: on it. it comes back when it needs you."}
    if path == "/api/accept":
        e = accept(project, task, reason, merging=bool(body.get("merge")))  # Merging from this click on
        # Accept and merge: your click. A fast-forward when the base branch is still the tip the task
        # began on; otherwise the base branch is merged in and the test gate runs again. Local, never pushed
        if body.get("merge"):
            from .accept import merge_now
            try:
                return {"message": merge_now(project, task), "merged": True}
            except ParallaxError as err:
                return {"message": f"accepted {task} as {e['data']['commit'][:7]}, but {err}. merge it yourself:",
                        "merge": merge_command(e)}
        return {"message": f"accepted {task} as {e['data']['commit'][:7]}. merge it yourself:",
                "merge": merge_command(e)}
    if path == "/api/ask":
        from . import ask
        return ask.answer(project, task, str(body.get("question") or ""))
    if path == "/api/reject":
        if body.get("drop"):
            if decide.decision(project, task) is not None:
                return {"message": decide.apply(project, task, "drop", reason)}
            if not reason.strip():
                raise ParallaxError("dropping a task needs a reason")
            project.ledger.append("task.rejected", "human", reason, task=task, was=project.task(task)["status"])
            return {"message": f"dropped {task}. it's out of the inbox."}
        dec = decide.decision(project, task)
        if dec is not None and any(o.name == "reject" for o in dec.options):
            return {"message": decide.apply(project, task, "reject", reason)}
        pilot.redraft(project, task, reason)
        return {"message": f"redrafting {task} from your reason. it comes back when it needs you."}
    if path == "/api/decide":
        return {"message": decide.apply(project, task, str(body.get("option") or ""), reason)}
    raise ParallaxError("not found")


ERROR_MESSAGE = "parallax hit an error. the terminal running parallax ui has the detail"


def _log_error(path: str) -> str:
    """The traceback goes to the server's terminal only; the page gets one fixed line."""
    print(f"parallax ui: error on {path}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
    return ERROR_MESSAGE


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
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", CSP)
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
            if path in STATIC:
                if self._allowed(needs_token=False):
                    name, ctype = STATIC[path]
                    self._send(200, (WEB / name).read_bytes(), ctype)
                return
            if path in COMPUTED:
                if self._allowed(needs_token=False):
                    render, ctype = COMPUTED[path]
                    self._send(200, render(), ctype)
                return
            if not self._allowed(needs_token=True):
                return
            try:
                parts = path.strip("/").split("/")
                if path == "/api/version":
                    st = os.stat(ui.project().ledger.path)
                    self._json(200, {"version": f"{st.st_size}-{st.st_mtime_ns}"})
                elif path == "/api/board":
                    p = ui.project()
                    self._json(200, {"project": p.root.name, **views.board(p)})
                elif len(parts) == 3 and parts[:2] == ["api", "task"]:
                    self._json(200, views.card(ui.project(), parts[2]))
                elif len(parts) == 5 and parts[:2] == ["api", "task"] and parts[3] == "shot":
                    self._send(200, views.shot(ui.project(), parts[2], parts[4]), "image/png")
                elif len(parts) == 5 and parts[:2] == ["api", "task"] and parts[3] == "doc":
                    self._json(200, {"text": views.document(ui.project(), parts[2], parts[4])})
                elif len(parts) == 5 and parts[:2] == ["api", "task"] and parts[3] == "ledger":
                    self._json(200, {"text": views.ledger_entry(ui.project(), parts[2], parts[4])})
                else:
                    self._json(404, {"error": "not found"})
            except (ParallaxError, ValueError) as err:
                self._json(400, {"error": str(err)})
            except Exception:  # never a dropped connection: the page says something broke, the terminal says what
                self._json(500, {"error": _log_error(self.path)})

        def do_POST(self):
            if not self._allowed(needs_token=True):
                return
            if not (self.headers.get("Content-Type") or "").startswith("application/json"):
                return self._json(415, {"error": "send json"})
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "bad content length"})
            if not 0 <= length <= MAX_BODY:
                return self._json(413, {"error": "too big"})
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                return self._json(400, {"error": "bad json"})
            if not isinstance(body, dict):
                return self._json(400, {"error": "send a json object"})
            try:
                self._json(200, act(ui.project(), self.path, body))
            except ParallaxError as err:
                self._json(404 if str(err) == "not found" else 400, {"error": str(err)})
            except Exception:
                self._json(500, {"error": _log_error(self.path)})

    return Handler
