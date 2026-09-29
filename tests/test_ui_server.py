import http.client
import json
import re
import subprocess
import sys
import threading

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from sandboxcheck import why_not
from parallax import build, pilot, preflight, show, views
from parallax.core import ROOT_ENV, TASK_ENV, ParallaxError, Project
from parallax.ui import CSP, ERROR_MESSAGE, MAX_BODY, UI, WEB
from test_lifecycle_gates import WANT, docs, make_key

NO_SANDBOX = why_not()  # None when the real sandbox starts here


@pytest.fixture
def proj(repo, monkeypatch):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: 9)
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    return Project.init(repo)


def ready_task(proj, work=WANT):
    tid = pilot.intake(proj, work)["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    assert build.run_mode(proj, tid, "pilot", FakeDrafter(docs()), lambda left, settings: maker, FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=good_probe) == "ready"
    return tid


@pytest.fixture
def server(proj):
    app = UI(proj.root)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    yield app, proj
    app.close()


def call(app, method, path, body=None, token=True, headers=None, raw=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    h = {"Host": f"127.0.0.1:{app.port}", "Content-Type": "application/json"}
    if token:
        h["X-Parallax-Token"] = app.token
    h.update(headers or {})
    payload = raw if raw is not None else (json.dumps(body) if body is not None else None)
    conn.request(method, path, body=payload, headers=h)
    res = conn.getresponse()
    data = res.read()
    ctype = res.getheader("Content-Type", "")
    return res.status, (json.loads(data) if ctype.startswith("application/json") else data), res


# the board and the card ---------------------------------------------------------------------------

def test_the_board_holds_every_task_by_state(proj):
    ready = ready_task(proj)
    working = pilot.intake(proj, "another thing")["task"]
    b = views.board(proj)
    assert list(b["columns"]) == ["drafting", "building", "checking", "ready", "needs you", "done"]
    assert [t["task"] for t in b["columns"]["ready"]] == [ready]
    assert [t["task"] for t in b["columns"]["drafting"]] == [working]
    assert b["count"] == 1 and [t["task"] for t in b["waiting"]] == [ready]
    assert b["waiting"][0]["line"].startswith("the checker passed") and b["waiting"][0]["kind"] == "ready"
    assert [t["task"] for t in b["working"]] == [working] and b["working"][0]["line"].startswith("drafters writing")


def test_the_card_is_exactly_parallax_show(proj):
    tid = ready_task(proj)
    c = views.card(proj, tid)
    text = show.report(proj, tid)
    assert c["report"]["bottom"] in text and c["report"]["bottom"].startswith("Ready: the checker passed")
    assert c["report"]["sections"]["Found"] == [line[2:] for line in text.split("Found\n", 1)[1].splitlines() if line.startswith("- ")]
    assert c["actions"] == {"kind": "ready"} and c["docs"] == ["intent", "plan"]
    assert "+ok" in views.document(proj, tid, "diff")
    assert views.document(proj, tid, "intent").startswith("Bottom line:")
    with pytest.raises(ValueError):
        views.document(proj, tid, "../../etc/passwd")


def test_a_decision_card_carries_its_options(proj):
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("stuck.raised", "parallax", "the budget cap ran out ($2.10 of $2.00 estimated)", task=tid,
                       budget=True)
    a = views.card(proj, tid)["actions"]
    assert a["kind"] == "decide" and a["recommend"] == "raise"
    assert [o["name"] for o in a["options"]] == ["raise", "drop"]


# the server: only the person at the page can act ------------------------------------------------------

def test_only_the_page_holder_can_use_the_api(server):
    app, proj = server
    assert call(app, "GET", "/api/board", token=False)[0] == 401
    assert call(app, "GET", "/api/board", headers={"X-Parallax-Token": "guess"})[0] == 401
    assert call(app, "GET", "/api/board", headers={"Host": "evil.example:80"})[0] == 403
    assert call(app, "GET", "/api/board", headers={"Origin": "http://evil.example"})[0] == 403
    assert call(app, "POST", "/api/do", {"work": "x"}, token=False)[0] == 401
    assert call(app, "GET", "/", token=False, headers={"Host": "evil.example"})[0] == 403  # DNS rebinding
    assert proj.tasks() == {}
    status, body, _ = call(app, "GET", "/api/board", headers={"Host": f"localhost:{app.port}"})
    assert status == 200  # the Windows browser reaches it as localhost


def test_the_page_is_served_locked_down(server):
    app, proj = server
    for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/app.css", "text/css")):
        status, raw, res = call(app, "GET", path, token=False)
        assert status == 200 and res.getheader("Content-Type").startswith(ctype)
        assert res.getheader("Content-Security-Policy") == CSP
        assert res.getheader("X-Frame-Options") == "DENY" and res.getheader("Referrer-Policy") == "no-referrer"
    assert "'unsafe-inline'" not in CSP and "script-src 'self'" in CSP and "frame-ancestors 'none'" in CSP
    assert app.url.startswith(f"http://127.0.0.1:{app.port}/#") and app.windows_url.startswith(f"http://localhost:{app.port}/#")


def test_the_page_has_no_inline_code_and_never_writes_agent_text_as_html():
    html = (WEB / "index.html").read_text()
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)  # every script is a file
    assert "<style" not in html and " style=" not in html
    assert not re.search(r"\son[a-z]+=", html)  # no inline handlers
    js = (WEB / "app.js").read_text()
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert risky not in js, risky
    assert "localStorage" not in js  # the token lives in the tab only
    assert "\u2014" not in html + js + (WEB / "app.css").read_text()


def test_version_changes_with_the_ledger(server):
    app, proj = server
    v1 = call(app, "GET", "/api/version")[1]["version"]
    proj.ledger.append("note", "human", "anything")
    assert call(app, "GET", "/api/version")[1]["version"] != v1


# actions, the same way the CLI does them --------------------------------------------------------------

def test_intake_from_the_box_starts_a_task(server):
    app, proj = server
    status, body, _ = call(app, "POST", "/api/do", {"work": "fix the typo in the README"})
    assert status == 200 and body["task"] in proj.tasks()
    assert proj.task(body["task"])["status"] == "drafting"
    assert call(app, "POST", "/api/do", {"work": "  "})[0] == 400


def test_accept_from_the_card_shows_the_merge_command(server):
    app, proj = server
    tid = ready_task(proj)
    status, body, _ = call(app, "POST", "/api/accept", {"task": tid})
    assert status == 200 and body["merge"] == f"git merge --ff-only {proj.task(tid)['branch']}"
    assert proj.task(tid)["status"] == "accepted"
    card = call(app, "GET", f"/api/task/{tid}")[1]
    assert card["merge"] == body["merge"] and card["actions"]["kind"] == "none"  # shown, never run


def test_reject_needs_a_reason_then_redrafts_or_drops(server):
    app, proj = server
    tid = ready_task(proj)
    status, body, _ = call(app, "POST", "/api/reject", {"task": tid, "reason": " "})
    assert status == 400 and "reason" in body["error"]
    assert call(app, "POST", "/api/reject", {"task": tid, "reason": "name the shell"})[0] == 200
    assert proj.task(tid)["status"] == "drafting"
    other = ready_task(proj, "something else")
    assert call(app, "POST", "/api/reject", {"task": other, "reason": "not needed", "drop": True})[0] == 200
    assert proj.task(other)["status"] == "rejected"


def test_decide_from_the_card(server):
    app, proj = server
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("disagreement.raised", "parallax", "extra.py changed but isn't in the plan's files", task=tid,
                       stage="scope")
    status, body, _ = call(app, "POST", "/api/decide", {"task": tid, "option": "accept"})
    assert status == 400 and "needs a reason" in body["error"]
    status, body, _ = call(app, "POST", "/api/decide", {"task": tid, "option": "drop", "reason": "not needed"})
    assert status == 200 and proj.task(tid)["status"] == "rejected"


def test_bad_requests_are_refused(server):
    app, proj = server
    assert call(app, "POST", "/api/do", raw="{nope")[0] == 400
    assert call(app, "POST", "/api/do", raw="[1]")[0] == 400
    assert call(app, "POST", "/api/do", {"work": "x"}, headers={"Content-Type": "text/plain"})[0] == 415
    assert call(app, "POST", "/api/merge", {"task": "x"})[0] == 404  # there is no merge endpoint
    assert call(app, "GET", "/api/task/nope")[0] == 400


def test_the_ui_wont_start_inside_a_task(proj, monkeypatch):
    monkeypatch.setenv(TASK_ENV, "abc123")
    monkeypatch.setenv(ROOT_ENV, str(proj.root))
    with pytest.raises(ParallaxError, match="tasks can't"):
        UI(proj.root)


@pytest.mark.skipif(NO_SANDBOX is not None, reason=NO_SANDBOX or "")
def test_sandboxed_bash_cant_reach_the_ui(server, tmp_path):
    """A second layer, behind the token: the maker's commands have no route to the page."""
    app, proj = server
    cfg = tmp_path / "srt.json"
    cfg.write_text(json.dumps({"network": {"allowedDomains": [], "deniedDomains": []},
                               "filesystem": {"allowWrite": [str(tmp_path)], "denyWrite": [], "denyRead": [], "allowRead": []}}))
    probe = (f"import socket; socket.create_connection(('127.0.0.1', {app.port}), timeout=3); print('REACHED')")
    out = subprocess.run(["srt", "--settings", str(cfg), "-c", f"python3 -c \"{probe}\""], cwd=tmp_path,
                         capture_output=True, text=True, timeout=120)
    assert "REACHED" not in out.stdout
    assert call(app, "GET", "/api/version")[0] == 200  # while the page itself is up


def test_version_holds_still_when_nothing_happens(server):
    """Live in the UI pass: every request touched the ledger, so every poll rebuilt the page."""
    app, proj = server
    first = call(app, "GET", "/api/version")[1]["version"]
    call(app, "GET", "/api/board")
    assert call(app, "GET", "/api/version")[1]["version"] == first


def test_the_link_survives_a_restart_and_stays_private(proj):
    from parallax.ui import home_port, link_path
    one = UI(proj.root)
    one.server.server_close()
    two = UI(proj.root)
    two.server.server_close()
    assert (two.port, two.token) == (one.port, one.token) and one.port == home_port(proj.root)
    path = link_path(proj.root)
    assert oct(path.stat().st_mode & 0o777) == "0o600" and oct(path.parent.stat().st_mode & 0o777) == "0o700"
    from parallax.approvals import key_path
    assert path.parent.parent == key_path().parent  # beside the key, where the sandbox can't read
    three = UI(proj.root, new_token=True)
    three.server.server_close()
    assert three.token != one.token and three.port == one.port


def test_a_crash_is_an_answer_not_a_dropped_connection(server, monkeypatch):
    app, proj = server
    tid = ready_task(proj)

    def broken(project, task_id):
        raise RuntimeError("boom")
    monkeypatch.setattr(views, "card", broken)
    status, body, _ = call(app, "GET", f"/api/task/{tid}")
    assert status == 500 and body["error"] == ERROR_MESSAGE and "boom" not in json.dumps(body)


def test_the_error_detail_goes_to_the_server_log_only(server, monkeypatch, capsys):
    app, proj = server
    tid = ready_task(proj)

    def broken(project, task_id):
        raise RuntimeError("secret path /home/you/x")
    monkeypatch.setattr(views, "card", broken)
    status, body, _ = call(app, "GET", f"/api/task/{tid}")
    err = capsys.readouterr().err
    assert status == 500 and "secret path" not in json.dumps(body)
    assert f"parallax ui: error on /api/task/{tid}" in err and "RuntimeError: secret path /home/you/x" in err


def test_the_board_request_runs_the_same_housekeeping_as_the_cli(server, monkeypatch):
    """Someone who only uses the UI never sees a dead task shown as building."""
    app, proj = server
    tid = ready_task(proj)
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=proc.pid, mode="build")
    proj.ledger.append("check.staged", "parallax", "", task=tid, tree="t1", base="b", files=["x"], lines=1,
                       binaries=[], symlinks=[], autorun=[], problems=[], risk_accepted=False)
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")  # running, with work behind it
    assert proj.task(tid)["status"] == "running"
    monkeypatch.setattr(build, "_spawn", lambda *a: 5)
    status, body, _ = call(app, "GET", "/api/board")
    assert status == 200
    assert proj.task(tid)["status"] == "stuck" and [i["task"] for i in body["waiting"]] == [tid]


def test_a_task_id_in_the_url_is_never_a_path(server):
    app, proj = server
    (proj.root / "docs").mkdir(exist_ok=True)
    (proj.root / "docs" / "intent.md").write_text("not yours\n")  # docs/tasks/../intent.md
    status, body, _ = call(app, "GET", "/api/task/../doc/intent")
    assert status == 400 and "not yours" not in json.dumps(body)
    assert call(app, "GET", "/api/task/../shot/x.png")[0] == 400


def test_a_bad_request_body_is_refused_not_crashed(server):
    app, _ = server
    assert call(app, "POST", "/api/do", headers={"Content-Length": "abc"})[0] == 400
    assert call(app, "POST", "/api/do", headers={"Content-Length": str(MAX_BODY + 1)})[0] == 413
    assert call(app, "GET", "/api/board")[0] == 200  # still serving
