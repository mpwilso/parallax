"""The UI tester: blind, sandboxed, off by default; its tests are hashed and rerun at every check."""
import json
import re
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import build, costs, show, uitest, views
from parallax.agents.base import AgentResult
from parallax.core import POLICY_FILE, Project
from parallax.policy import Policy
from test_m8 import docs, make_key

UI_POLICY = """[actions]
[ui_tester]
enabled = true
start = "python3 tests/ui_app.py 8765"
url = "http://127.0.0.1:8765/#tok"
paths = ["README.md"]
"""
SPEC = "const { test, expect } = require('@playwright/test');\ntest('opens', async ({ page }) => { await page.goto(process.env.APP_URL); });\n"


class FakeTester:
    """Writes one flow test and a screenshot, like the real one; remembers everything it was given."""

    def __init__(self, write=True, works=True, cost=0.3):
        self.write, self.works, self.cost = write, works, cost
        self.calls = []

    def __call__(self, limit, model):
        self.limit, self.model = limit, model
        return self

    def run(self, goal, cwd, server, allowed):
        self.calls.append({"goal": goal, "cwd": Path(cwd), "server": server, "allowed": allowed})
        if self.write:
            (Path(cwd) / "flows").mkdir()
            (Path(cwd) / "flows" / "opens.spec.js").write_text(SPEC)
            (Path(cwd) / "opens.png").write_bytes(b"\x89PNG fake")
        reply = {"flows": [{"outcome": 1, "name": "opens", "works": self.works, "saw": "the page opens"}],
                 "not_looked_at": "the page on a phone"}
        return AgentResult("done", "walked it.\n" + json.dumps(reply), self.cost)


def flow_runner(results):
    """A stand-in for the flows run in srt: results is a list of per-run lists of (name, ok)."""
    calls = []

    def run(config, cwd, script, env):
        calls.append((script, env))
        cfg = Path(re.search(r"--config (\S+)", script).group(1).strip("'"))
        junit = Path(json.loads(cfg.read_text().removeprefix("module.exports = ").rstrip(";\n"))["reporter"][0][1]["outputFile"])
        junit.parent.mkdir(parents=True, exist_ok=True)
        cases = results.pop(0) if len(results) > 1 else results[0]
        if cases == "app":
            (Path(env["HOME"]) / "app.log").write_text(f"Traceback: boom\n{uitest.NO_ANSWER} http://127.0.0.1:8765/ within 60s\n")
            return uitest.APP_FAILED, ""
        junit.write_text("<testsuites><testsuite>" + "".join(
            f'<testcase classname="opens.spec.js" name="{n}">' + ("" if ok else '<failure message="expected Ready"/>') + "</testcase>"
            for n, ok in cases) + "</testsuite></testsuites>")
        return (0 if all(ok for _, ok in cases) else 1), "1 passed"
    run.calls = calls
    return run


@pytest.fixture
def proj(repo, monkeypatch, tmp_path):
    (repo / POLICY_FILE).write_text(UI_POLICY)
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    tools = tmp_path / "tools"
    monkeypatch.setattr(uitest, "ensure_tools", lambda: uitest.Tools(tools, tools / "chrome"))
    return Project.init(repo)


def pilot(proj, tester, flows, maker=None):
    from parallax import pilot as p
    tid = p.intake(proj, "fix the README")["task"]
    maker = maker or ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    return tid, build.run_mode(proj, tid, "pilot", FakeDrafter(docs()), lambda left, s: maker, FakeChecker(),
                               test_runner=junit_runner(), preflight_runner=good_probe)


def kinds(proj, kind, tid=None):
    return [e for e in proj.ledger.entries() if e["kind"] == kind and (tid is None or e["data"].get("task") == tid)]


# the policy ---------------------------------------------------------------------------------------------

def test_off_by_default_and_needs_its_settings_when_on():
    assert Policy({}).ui_tester["enabled"] is False
    for bad, says in ((dict(enabled=True, url="http://127.0.0.1:1/", paths=["a"]), "start"),
                      (dict(enabled=True, start="x", url="https://example.com/", paths=["a"]), "on this machine"),
                      (dict(enabled=True, start="x", url="http://127.0.0.1:1/", paths=[]), "paths"),
                      (dict(tests="docs/tasks/x"), "inside the repo"), (dict(tests="../out"), "inside the repo"),
                      (dict(max_usd=0), "above 0"), (dict(nope=1), "unknown")):
        with pytest.raises(ValueError, match=says):
            Policy({}, ui_tester=bad)


def test_off_means_it_never_runs(repo, monkeypatch):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    proj = Project.init(repo)
    tid, status = pilot(proj, None, None)  # the conftest's refusing tester would fail this if it ran
    assert status == "ready" and not kinds(proj, "uitest.started")


def test_only_plans_that_change_the_ui(proj):
    assert uitest.applies(proj, {"files": ["README.md"]}) and not uitest.applies(proj, {"files": ["setup.py"]})


# a run -----------------------------------------------------------------------------------------------------

def test_the_tester_is_blind_sandboxed_and_its_tests_are_hashed_and_run(proj, monkeypatch):
    tester = FakeTester()
    runner = flow_runner([[("opens", True)]])
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", runner)
    tid, status = pilot(proj, tester, runner)
    assert status == "ready"
    call = tester.calls[0]
    goal = call["goal"]
    assert "A new user on WSL can follow them." in goal and "http://127.0.0.1:8765/#tok" in goal
    for never in ("Rewrite the install section", "tests/test_readme.py", "done", "README.md\n"):  # no plan, no diff, no notes
        assert never not in goal
    assert not any(call["cwd"].iterdir()) or {p.name for p in call["cwd"].iterdir()} <= {"flows", "opens.png", "tmp", "app.log"}
    srv = call["server"]
    assert srv["command"] == "srt" and "--allowed-origins http://127.0.0.1:8765" in srv["args"][-1]
    cfg = json.loads(Path(srv["args"][1]).read_text())
    assert cfg["network"]["allowedDomains"] == [] and str(Path.home()) in cfg["filesystem"]["denyRead"]
    assert tester.limit <= proj.policy.ui_tester["max_usd"]

    rec = kinds(proj, "uitest.recorded", tid)[-1]["data"]
    rel = f"tests/ui_flows/{tid}/opens.spec.js"
    wt = Path(proj.task(tid)["worktree"])
    assert rel in rec["files"] and (wt / rel).read_text() == SPEC
    staged = kinds(proj, "check.staged", tid)[-1]["data"]
    assert rel in staged["files"] and staged["problems"] == []  # its tests count as planned
    ran = kinds(proj, "flows.recorded", tid)[-1]["data"]
    assert (ran["passed"], ran["total"]) == (1, 1) and runner.calls
    assert costs.spent(proj, tid) >= 0.3  # its cost counts against the task's cap
    text = show.report(proj, tid)
    assert "UI flows: 1 of 1 pass" in text and "the page on a phone" in text
    card = views.card(proj, tid)
    assert card["shots"] == [{"name": "opens.png", "caption": "the page opens", "works": True}]
    assert views.shot(proj, tid, "opens.png") == b"\x89PNG fake"
    with pytest.raises(ValueError):
        views.shot(proj, tid, "../../key")


def test_a_failing_flow_is_reworked_and_the_tester_runs_once(proj, monkeypatch):
    tester = FakeTester(works=False)
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", False)], [("opens", True)]]))
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    tid, status = pilot(proj, tester, None, maker)
    assert status == "ready" and len(tester.calls) == 1
    rework = kinds(proj, "rework.started", tid)[0]["reason"]
    assert 'the UI flow "opens" fails: expected Ready' in rework


def test_the_maker_cant_write_its_tests_and_a_change_comes_to_you(proj, monkeypatch):
    monkeypatch.setattr(uitest, "TESTER", FakeTester())
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", False)]]))
    rel = None

    def sneaky(cwd):
        spec = next(Path(cwd).glob("tests/ui_flows/*/opens.spec.js"))
        spec.write_text(SPEC.replace("opens", "skipped"))
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n"), ("call", sneaky)])
    from parallax import pilot as p
    tid = p.intake(proj, "fix the README")["task"]
    first = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    makers = iter([first, maker])
    status = build.run_mode(proj, tid, "pilot", FakeDrafter(docs()), lambda left, s: next(makers), FakeChecker(),
                            test_runner=junit_runner(), preflight_runner=good_probe)
    assert status == "disputed"
    raised = kinds(proj, "disagreement.raised", tid)[-1]
    assert raised["data"]["stage"] == "guard" and "the UI tester's tests changed" in raised["reason"]
    rel = f"tests/ui_flows/{tid}/opens.spec.js"
    prepared = build.prepare(proj, tid, setup=False, launching=False)
    assert str(prepared.worktree / rel) in prepared.rules.deny_write  # both layers deny it to the maker


def test_an_app_that_wont_start_is_the_makers_to_fix(proj, monkeypatch):
    monkeypatch.setattr(uitest, "TESTER", FakeTester())
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner(["app", [("opens", True)]]))
    tid, status = pilot(proj, None, None)
    assert status == "ready"
    assert "the app didn't start for the UI flow tests" in kinds(proj, "rework.started", tid)[0]["reason"]
    assert "Traceback: boom" in kinds(proj, "rework.started", tid)[0]["reason"]


def test_it_fails_closed_when_the_browser_cant_run_or_it_writes_nothing(proj, monkeypatch):
    def broken():
        raise uitest.UITestError("the browser can't start without libnss3.so")
    monkeypatch.setattr(uitest, "ensure_tools", broken)
    tid, status = pilot(proj, None, None)
    assert status == "disputed" and "the UI tester couldn't run: the browser can't start" in kinds(proj, "disagreement.raised", tid)[-1]["reason"]

    monkeypatch.setattr(uitest, "ensure_tools", lambda: uitest.Tools(Path("/nowhere"), Path("/nowhere/chrome")))
    monkeypatch.setattr(uitest, "TESTER", FakeTester(write=False))
    tid, status = pilot(proj, None, None)
    assert status == "disputed" and "the UI tester wrote no tests" in kinds(proj, "disagreement.raised", tid)[-1]["reason"]


def test_its_tools_are_the_browser_and_its_own_folder(tmp_path):
    ok = lambda tool, inp=None: uitest.allowed(tool, inp or {}, tmp_path)  # noqa: E731
    assert ok("mcp__playwright__browser_navigate") and ok("mcp__playwright__browser_take_screenshot")
    assert ok("Write", {"file_path": str(tmp_path / "flows" / "a.spec.js")})
    for tool, inp in (("Bash", {"command": "ls"}), ("mcp__playwright__browser_file_upload", {}),
                      ("mcp__playwright__browser_install", {}), ("Read", {"file_path": "/etc/passwd"}),
                      ("Write", {"file_path": str(tmp_path / ".." / "x")}), ("WebFetch", {"url": "https://x"}),
                      ("mcp__other__thing", {})):
        assert not ok(tool, inp), tool


def test_junit_from_the_playwright_runner(tmp_path):
    j = tmp_path / "j.xml"
    j.write_text('<testsuites><testsuite><testcase classname="a.spec.js" name="ok"/>'
                 '<testcase classname="a.spec.js" name="bad"><failure message="expected 1"/></testcase>'
                 '<testcase classname="a.spec.js" name="skip"><skipped/></testcase></testsuite></testsuites>')
    assert uitest.parse_junit(j) == [{"file": "a.spec.js", "name": "ok", "ok": True, "message": ""},
                                     {"file": "a.spec.js", "name": "bad", "ok": False, "message": "expected 1"}]

