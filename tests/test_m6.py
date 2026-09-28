import http.client
import json
import shlex
import subprocess
import threading

import pytest

from parallax import views
from parallax.core import POLICY_FILE, ROOT_ENV, TASK_ENV, ParallaxError, Project
from parallax.evidence import raise_promotions
from parallax.ui import UI
from seed import seed_all_kinds


@pytest.fixture
def seeded(repo):
    ids = seed_all_kinds(repo)
    return Project(repo), ids


@pytest.fixture
def server(seeded):
    proj, ids = seeded
    app = UI(proj.root)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    yield app, proj, ids
    app.close()


def call(app, method, path, body=None, token=True, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
    h = {"Host": f"127.0.0.1:{app.port}", "Content-Type": "application/json"}
    if token:
        h["X-Parallax-Token"] = app.token
    h.update(headers or {})
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    res = conn.getresponse()
    raw = res.read()
    return res.status, (json.loads(raw) if res.getheader("Content-Type", "").startswith("application/json") else raw), res


# views -------------------------------------------------------------------------------

def test_every_kind_has_its_context_and_plain_choices(seeded):
    proj, ids = seeded
    items = {i["id"]: i for g in views.inbox_view(proj)["groups"] for i in g["items"]}
    assert {i["kind"] for i in items.values()} == set(views.CHOICES)

    d = items[ids["disagreement"]]
    assert d["choices"] == ["Side with the maker", "Side with the checker"]
    assert d["context"]["findings"] == ["add() now ignores negative numbers"]
    assert "+    return a + b if a > 0 else b" in d["context"]["diff"]
    assert d["context"]["maker_summary"].startswith("Fixed add()")

    p = items[ids["permission"]]
    assert p["headline"] == "wants to run git status" and p["context"]["waiting"] is True
    assert p["recommendation"] == {"option": "approve", "why": "read-only command, harmless"}

    promo = items[ids["promotion"]]
    assert len(promo["context"]["approvals"]) == 10 and promo["choices"] == ["Promote", "Keep asking"]

    law = items[ids["law"]]
    assert [e["reason"] for e in law["context"]["evidence"]][0] == "I review before anything is committed"
    assert law["context"]["rule"]["ruling"] == "deny" and law["context"]["evidence"][0]["about"].startswith("git.commit")

    assert items[ids["stuck"]]["context"]["refusals"][0]["why"].startswith("protected file")
    assert items[ids["proposal"]]["context"]["plan"] is True


def test_task_view_has_timeline_diff_and_merge_hint(seeded):
    proj, ids = seeded
    t = views.task_view(proj, ids["ready_task"])
    assert t["status"] == "ready"
    steps = t["merge"].splitlines()  # the maker's work isn't committed yet, so commit before merging
    assert steps[0].endswith("add -A") and "commit -m" in steps[1] and steps[2].startswith("git merge parallax/")
    assert "+def mul(a, b):" in t["diff"]
    texts = [e["text"] for e in t["timeline"]]
    assert "created" in texts and any(x.startswith("checker (diff): pass") for x in texts)
    assert views.tasks_view(proj)[0]["goal"] == "update the changelog"  # newest first


def test_the_merge_steps_really_merge(seeded):
    proj, ids = seeded
    t = proj.task(ids["ready_task"])
    for step in views.merge_steps(t).splitlines():
        args = step.split(" ", 1)[1]
        cmd = ["git", "-c", "user.email=t@t", "-c", "user.name=t"] + shlex.split(args)
        subprocess.run(cmd, cwd=proj.root, check=True, capture_output=True)
    assert (proj.root / "mul.py").read_text().startswith("def mul")


def test_views_keep_non_ascii(repo):
    proj = Project.init(repo)
    t = proj.new_task("tabla ╒═╕")
    (proj.root / ".parallax" / "worktrees" / t["task"] / "t.txt").write_text("│ é │\n", encoding="utf-8")
    assert "│ é │" in views.task_view(proj, t["task"])["diff"]


# server ------------------------------------------------------------------------------

def test_only_the_page_holder_can_use_the_api(server):
    app, proj, ids = server
    assert call(app, "GET", "/api/state", token=False)[0] == 401
    assert call(app, "GET", "/api/state", headers={"X-Parallax-Token": "guess"})[0] == 401
    assert call(app, "GET", "/api/state", headers={"Host": "evil.example:80"})[0] == 403
    assert call(app, "GET", "/api/state", headers={"Origin": "http://evil.example"})[0] == 403
    status, body, _ = call(app, "POST", "/api/resolve", {"ids": [ids["permission"]], "approve": True, "reason": "x"},
                           token=False)
    assert status == 401 and proj.decision_outcome(ids["permission"]) is None


def test_the_page_is_served_locked_down(server):
    app, proj, ids = server
    status, raw, res = call(app, "GET", "/", token=False)
    assert status == 200 and b'id="list"' in raw and b"X-Parallax-Token" in raw
    assert res.getheader("X-Frame-Options") == "DENY" and "frame-ancestors 'none'" in res.getheader("Content-Security-Policy")
    assert app.url.startswith(f"http://127.0.0.1:{app.port}/#")


def test_state_matches_the_inbox(server):
    app, proj, ids = server
    status, body, _ = call(app, "GET", "/api/state")
    assert status == 200 and body["inbox"]["count"] == len(proj.inbox())
    assert body["project"] == proj.root.name and body["tasks"]


def test_deciding_needs_a_reason_and_can_batch(server):
    app, proj, ids = server
    assert call(app, "POST", "/api/resolve", {"ids": [ids["permission"]], "approve": True, "reason": " "})[0] == 400
    extra = proj.check(proj.new_task("x")["task"], "git.commit", "commit")["entry"]["id"]
    status, body, _ = call(app, "POST", "/api/resolve",
                           {"ids": [ids["permission"], extra], "approve": False, "reason": "not now"})
    assert status == 200 and [r["ok"] for r in body["results"]] == [True, True]
    assert proj.decision_outcome(extra)["reason"] == "not now" and proj.decision_outcome(extra)["actor"] == "human"


def test_deciding_reports_what_changed(server):
    app, proj, ids = server
    _, body, _ = call(app, "POST", "/api/resolve", {"ids": [ids["promotion"]], "approve": True, "reason": "safe"})
    assert body["results"][0]["changed"].startswith("policy updated: exact shell.run")
    _, body, _ = call(app, "POST", "/api/resolve", {"ids": [ids["proposal"]], "approve": True, "reason": "yes"})
    assert body["results"][0]["task"]


def test_an_unverifiable_edit_shows_the_snippet_and_decides_nothing(repo):
    (repo / POLICY_FILE).write_text('exact = { "shell.run" = { "ls" = "deny" } }\n[actions]\n"shell.run" = "ask"\n')
    proj = Project.init(repo)
    t = proj.new_task("x")
    for _ in range(10):
        proj.resolve(proj.check(t["task"], "shell.run", "pytest -q")["entry"]["id"], True, "ok")
    [p] = raise_promotions(proj)
    app = UI(repo)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    try:
        _, body, _ = call(app, "POST", "/api/resolve", {"ids": [p["id"]], "approve": True, "reason": "safe"})
    finally:
        app.close()
    r = body["results"][0]
    assert r["ok"] is False and '"pytest -q" = "allow"' in r["snippet"]
    assert p["id"] in [e["id"] for e in proj.inbox()]


def test_version_changes_with_the_ledger(server):
    app, proj, ids = server
    v1 = call(app, "GET", "/api/version")[1]["version"]
    proj.ledger.append("note", "human", "anything")
    assert call(app, "GET", "/api/version")[1]["version"] != v1


def test_ui_wont_start_inside_a_task(seeded, monkeypatch):
    proj, ids = seeded
    monkeypatch.setenv(TASK_ENV, "abc123")
    monkeypatch.setenv(ROOT_ENV, str(proj.root))
    with pytest.raises(ParallaxError, match="tasks can't"):
        UI(proj.root)
