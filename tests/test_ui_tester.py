"""The UI tester: blind, sandboxed, off by default; its tests are hashed and rerun at every check."""
import json
import re
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import build, costs, lint, memcap, show, uitest, views
from parallax.agents.base import AgentResult
from parallax import lifecycle
from parallax.core import POLICY_FILE, Project
from parallax.policy import Policy
from test_lifecycle_gates import docs, make_key

UI_POLICY = """[launch]
auto_launch_usd = 5.0  # room for the tester's share: $0.20 drafting, twice $0.90, $0.50
[ui_tester]
enabled = true
start = "python3 tests/ui_app.py 8765"
url = "http://127.0.0.1:8765/#tok"
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
        (Path(cwd) / uitest.APP_UP).touch()  # the wrapper's mark that the app answered
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


def flows_docs():
    """The fixture docs, with a plan that names outcome 1 as a user flow."""
    d = docs()
    d["plan"] = d["plan"].replace("covers = {", 'user_flows = ["1"]\ncovers = {')
    return d


def pilot(proj, tester, flows, maker=None):
    from parallax import pilot as p
    tid = p.intake(proj, "fix the README")["task"]
    maker = maker or ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    return tid, build.run_mode(proj, tid, "pilot", FakeDrafter(flows_docs()), lambda left, s: maker, FakeChecker(),
                               test_runner=junit_runner(), preflight_runner=good_probe)


def kinds(proj, kind, tid=None):
    return [e for e in proj.ledger.entries() if e["kind"] == kind and (tid is None or e["data"].get("task") == tid)]


# the policy ---------------------------------------------------------------------------------------------

def test_off_by_default_and_needs_its_settings_when_on():
    assert Policy({}).ui_tester["enabled"] is False
    for bad, says in ((dict(enabled=True, url="http://127.0.0.1:1/"), "start"),
                      (dict(enabled=True, start="x", url="https://example.com/"), "on this machine"),
                      (dict(max_usd=0), "above 0"), (dict(nope=1), "unknown")):
        with pytest.raises(ValueError, match=says):
            Policy({}, ui_tester=bad)


def test_off_means_it_never_runs(repo, monkeypatch):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    proj = Project.init(repo)
    tid, status = pilot(proj, None, None)  # the conftest's refusing tester would fail this if it ran
    assert status == "ready" and not kinds(proj, "uitest.started")


def test_only_plans_that_name_user_flows(proj):
    """Touching a UI file isn't enough: the plan says which outcomes a person goes through."""
    assert uitest.applies(proj, {"files": ["x"], "user_flows": ["1"]})
    assert not uitest.applies(proj, {"files": ["parallax/web/app.js"], "user_flows": []})


# a run -----------------------------------------------------------------------------------------------------

def test_the_tester_is_blind_sandboxed_and_its_tests_are_hashed_and_run(proj, monkeypatch):
    tester = FakeTester()
    runner = flow_runner([[("opens", True)], [("opens", True)]])
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", runner)
    tid, status = pilot(proj, tester, runner)
    assert status == "ready"
    call = tester.calls[0]
    goal = call["goal"]
    assert "A new user on WSL can follow them." in goal and "http://127.0.0.1:8765/#tok" in goal
    for never in ("Rewrite the install section", "tests/test_readme.py", "done", "README.md\n"):  # no plan, no diff, no notes
        assert never not in goal
    assert not any(call["cwd"].iterdir()) or {p.name for p in call["cwd"].iterdir()} <= {"flows", "opens.png", "tmp", "app.log", uitest.APP_UP}
    srv = call["server"]
    # the app and the browser run in srt, all of it under the memory cap, with the cap's one-line message
    assert srv["command"] == sys.executable and srv["args"][:4] == ["-I", str(Path(memcap.__file__).resolve()),
                                                                     str(memcap.COMMAND), "--"]
    inner = srv["args"][4:]
    assert inner[0] == "srt" and "--allowed-origins http://127.0.0.1:8765" in inner[-1]
    assert uitest.MCP_VERSION == "0.0.70", ("the MCP pin moved. THREAT_MODEL's claim that the tester's browser blocks file: URLs "
                                             "relies on this version's default (--allow-unrestricted-file-access off). recheck it "
                                             "with the new version's --help, then update the claim and this pin together")
    assert "--allow-unrestricted-file-access" not in srv["args"][-1]  # the MCP blocks file: URLs unless told otherwise
    cfg = json.loads(Path(inner[2]).read_text())
    assert cfg["network"]["allowedDomains"] == [] and str(Path.home()) in cfg["filesystem"]["denyRead"]
    assert tester.limit <= proj.policy.ui_tester["max_usd"]

    rec = kinds(proj, "uitest.recorded", tid)[-1]["data"]
    rel = f"docs/tasks/{tid}/ui_flows/opens.spec.js"  # Parallax's alone, and out of the checker's diff
    assert rel in rec["files"] and (proj.root / rel).read_text() == SPEC
    staged = kinds(proj, "check.staged", tid)[-1]["data"]
    assert rel not in staged["files"] and staged["problems"] == []
    ran = kinds(proj, "flows.recorded", tid)[-1]["data"]
    assert (ran["passed"], ran["total"]) == (1, 1) and runner.calls
    assert costs.spent(proj, tid) >= 0.3  # its cost counts against the task's cap
    text = show.report(proj, tid)
    assert "1 of 1 UI flow tests passed." in text and "the page on a phone" in text
    card = views.card(proj, tid)
    assert card["shots"] == [{"name": "opens.png", "caption": "the page opens", "works": True}]
    assert views.shot(proj, tid, "opens.png") == b"\x89PNG fake"
    with pytest.raises(ValueError):
        views.shot(proj, tid, "../../key")


def test_a_failing_flow_is_reworked_and_the_tester_runs_once(proj, monkeypatch):
    tester = FakeTester(works=False)
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", False)], [("opens", False)], [("opens", True)]]))
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    tid, status = pilot(proj, tester, None, maker)
    assert status == "ready" and len(tester.calls) == 1
    rework = kinds(proj, "rework.started", tid)[0]["reason"]
    assert 'the UI flow "opens" fails: expected Ready' in rework


def test_the_maker_cant_write_its_tests_and_a_change_comes_to_you(proj, monkeypatch):
    monkeypatch.setattr(uitest, "TESTER", FakeTester())
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)], [("opens", False)]]))

    def tamper(cwd):  # anything that changes a kept test between checks
        spec = next(proj.root.glob("docs/tasks/*/ui_flows/opens.spec.js"))
        spec.write_text(SPEC.replace("opens", "skipped"))
    from parallax import pilot as p
    tid = p.intake(proj, "fix the README")["task"]
    makers = iter([ScriptedAgent(steps=[("write", "README.md", "ok\n")]),
                   ScriptedAgent(steps=[("write", "README.md", "ok again\n"), ("call", tamper)])])
    status = build.run_mode(proj, tid, "pilot", FakeDrafter(flows_docs()), lambda left, s: next(makers), FakeChecker(),
                            test_runner=junit_runner(), preflight_runner=good_probe)
    assert status == "disputed"
    raised = kinds(proj, "disagreement.raised", tid)[-1]
    assert raised["data"]["stage"] == "guard" and "Field's tests changed" in raised["reason"]
    assert not list(Path(proj.task(tid)["worktree"]).glob("docs/tasks/**/*.spec.js"))  # never in the maker's reach
    prepared = build.prepare(proj, tid, setup=False, launching=False)
    assert any(d.endswith("/docs/tasks") for d in prepared.rules.deny_write)


def test_an_app_that_wont_start_is_the_makers_to_fix(proj, monkeypatch):
    monkeypatch.setattr(uitest, "TESTER", FakeTester())
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)], "app", [("opens", True)]]))
    tid, status = pilot(proj, None, None)
    assert status == "ready"
    assert "the app didn't start for the UI flow tests" in kinds(proj, "rework.started", tid)[0]["reason"]
    assert "Traceback: boom" in kinds(proj, "rework.started", tid)[0]["reason"]


def test_it_fails_closed_when_the_browser_cant_run_or_it_writes_nothing(proj, monkeypatch):
    def broken():
        raise uitest.UITestError("the browser can't start without libnss3.so")
    monkeypatch.setattr(uitest, "ensure_tools", broken)
    tid, status = pilot(proj, None, None)
    assert status == "disputed" and "Field (the UI tester) couldn't run: the browser can't start" in kinds(proj, "disagreement.raised", tid)[-1]["reason"]

    monkeypatch.setattr(uitest, "ensure_tools", lambda: uitest.Tools(Path("/nowhere"), Path("/nowhere/chrome")))
    monkeypatch.setattr(uitest, "TESTER", FakeTester(write=False))
    tid, status = pilot(proj, None, None)
    assert status == "disputed" and "Field (the UI tester) wrote no tests" in kinds(proj, "disagreement.raised", tid)[-1]["reason"]


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



def test_a_repos_own_browser_tests_get_the_pinned_browser_in_the_check(repo):
    """a live run: this repo's plan tests drive Chromium, and the check's sandbox had none."""
    from parallax import testrun, tree
    shell = uitest.tools_dir() / "browsers" / "chromium_headless_shell-1" / "linux" / "chrome-headless-shell"
    shell.parent.mkdir(parents=True)
    shell.write_text("")
    seen = {}

    def runner(config, cwd, cmd, env):
        seen.update(env=env, cfg=json.loads(Path(config).read_text()))
        return 0, ""
    base = tree.stage(repo, "HEAD", repo / ".git" / "i").tree
    head = __import__("subprocess").run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    plan = {"tests": [], "outside_reads": [], "domains": []}
    testrun.run(repo, head, base, plan, repo.parent / "home", None, {"PATH": "/usr/bin"}, "true {junit} {tests}", runner)
    assert seen["env"]["PARALLAX_BROWSER"] == str(shell)
    assert str(uitest.tools_dir()) in seen["cfg"]["filesystem"]["allowRead"]


def test_tests_written_without_ever_seeing_the_app_dont_count(proj, monkeypatch):
    """d7f384: the app never started, the tester wrote four tests blind, and they were kept."""
    class Blind(FakeTester):
        def run(self, goal, cwd, server, allowed):
            out = super().run(goal, cwd, server, allowed)
            (Path(cwd) / uitest.APP_UP).unlink()
            (Path(cwd) / "app.log").write_text("fatal: unknown error occurred while reading the configuration files\n")
            return out
    monkeypatch.setattr(uitest, "TESTER", Blind())
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)]]))
    tid, status = pilot(proj, None, None)
    assert kinds(proj, "uitest.failed", tid) and not kinds(proj, "uitest.recorded", tid)
    assert "the app didn't start for Field: fatal: unknown error" in kinds(proj, "rework.started", tid)[0]["reason"]


def test_a_test_that_fails_on_the_build_it_describes_is_dropped_not_reworked(proj, monkeypatch):
    """964571: the tester's tests failed on the very build it had used, and the maker was sent to fix them."""
    monkeypatch.setattr(uitest, "TESTER", FakeTester(works=True))
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", False)]]))
    tid, status = pilot(proj, None, None)
    assert status == "disputed" and not kinds(proj, "rework.started", tid)
    assert "none of Field's tests passed on the build it described" in kinds(proj, "disagreement.raised", tid)[-1]["reason"]


def test_its_own_em_dash_is_not_the_makers_finding(proj, monkeypatch):
    (proj.root / POLICY_FILE).write_text((proj.root / POLICY_FILE).read_text() + "\n[check]\nno_em_dashes = true\n")
    proj.reload_policy()  # Parallax's own rule on, so this shows the tester's file is exempt from it
    class Dashing(FakeTester):
        def run(self, goal, cwd, server, allowed):
            out = super().run(goal, cwd, server, allowed)
            spec = Path(cwd) / "flows" / "opens.spec.js"
            spec.write_text(spec.read_text() + "// a \u2014 b\n")
            return out
    monkeypatch.setattr(uitest, "TESTER", Dashing())
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)]]))
    tid, status = pilot(proj, None, None)
    assert status == "ready" and not kinds(proj, "check.found", tid)


def test_a_fenced_reply_is_read():
    assert uitest._reply('done.\n```json\n{"flows": [{"name": "a", "works": true}]}\n```')["flows"][0]["works"] is True


def test_a_tester_test_that_still_fails_after_a_rework_comes_to_you(proj, monkeypatch):
    """2da12f: the tester's test counted <article> rows the page never had; no rework could fix it."""
    monkeypatch.setattr(uitest, "TESTER", FakeTester(works=False))
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", False)], [("opens", False)], [("opens", False)],
                                                            [("opens", True)]]))
    tid, status = pilot(proj, None, None)
    assert status == "disputed" and len(kinds(proj, "rework.started", tid)) == 1
    from parallax import decide
    dec = decide.decision(proj, tid)
    assert dec.kind == "flows" and [o.name for o in dec.options] == ["remove", "reject", "drop"]
    rel = f"docs/tasks/{tid}/ui_flows/opens.spec.js"
    assert dec.item["data"]["flow_files"] == [rel] and decide.flow_files(dec.item) == [rel]  # apart from scope files
    with pytest.raises(Exception, match="needs a reason"):
        decide.apply(proj, tid, "remove", spawn=lambda *a: 9)
    decide.apply(proj, tid, "remove", "it counts <article> rows; the page has none", spawn=lambda *a: 9)
    assert not (proj.root / rel).exists()
    assert uitest.guarded(proj, tid) == [] and uitest.tampered(proj, tid) == []


def test_every_accepted_tasks_flows_rerun_on_later_tasks(proj):
    import subprocess
    old = proj.root / "docs" / "tasks" / "aaa111" / "ui_flows" / "old.spec.js"
    old.parent.mkdir(parents=True)
    old.write_text(SPEC)
    subprocess.run(["git", "-C", str(proj.root), "add", "-A", "docs/tasks"], check=True)
    subprocess.run(["git", "-C", str(proj.root), "commit", "-qm", "an accepted task"], check=True)
    from parallax import pilot as p
    tid = p.intake(proj, "fix the README")["task"]
    t = proj.task(tid)
    found = uitest.specs(proj, tid, Path(t["worktree"]), t["base"])
    assert list(found) == ["docs/tasks/aaa111/ui_flows/old.spec.js"] and found["docs/tasks/aaa111/ui_flows/old.spec.js"] == SPEC.encode()


def test_the_tester_sees_only_the_outcomes_the_plan_names(proj, monkeypatch):
    from parallax import planfit
    intent = docs()["intent"].replace("1. asked: A new user on WSL can follow them.",
                                      "1. asked: A new user on WSL can follow them.\n2. inferred: The page lists the steps in order.")
    plan = {**lint.plan_block(flows_docs()["plan"])[0], "user_flows": ["3"]}
    assert "user_flows names outcome 3, which the intent doesn't have" in planfit.problems(intent, plan, 0.2, proj.policy.budget)
    from parallax import pilot as p
    tid = p.intake(proj, "x")["task"]
    d = flows_docs()
    d["intent"] = intent
    d["plan"] = d["plan"].replace('user_flows = ["1"]', 'user_flows = ["2"]').replace(
        'covers = { "1" = ["tests/test_readme.py"] }', 'covers = { "1" = ["tests/test_readme.py"], "2" = ["tests/test_readme.py"] }')
    p.draft_until_fit(proj, tid, FakeDrafter(d))
    assert uitest.outcomes(proj, tid) == "2. The page lists the steps in order."


def test_plans_without_the_field_still_read():
    data, _, why = lint.plan_block(docs()["plan"])
    assert why is None and data["user_flows"] == [] and lint.check_plan_data(data) == []



def test_the_testers_limit_is_its_reserve_and_it_stops_there(proj, monkeypatch):
    """The cap keeps max_usd for the tester, and the tester may spend max_usd: the same number."""
    from parallax import pilot as p
    from parallax.agents import claude

    class Broke(FakeTester):  # what the SDK does at max_budget_usd: stops, with nothing written
        def run(self, goal, cwd, server, allowed):
            (Path(cwd) / uitest.APP_UP).touch()
            return AgentResult("error", f"stopped at its budget (${self.limit})", self.limit)
    tester = Broke()
    monkeypatch.setattr(uitest, "TESTER", tester)
    tid, status = pilot(proj, None, None)
    plan = lifecycle.plan_data(proj, tid)
    assert tester.limit == proj.policy.ui_tester["max_usd"] == p._reserve(proj, plan) == 0.5
    assert status == "disputed" and "Field (the UI tester) wrote no tests (error)" in kinds(proj, "disagreement.raised", tid)[-1]["reason"]
    assert kinds(proj, "uitest.failed", tid)[-1]["data"]["cost_usd"] == 0.5  # what it spent counts

    class SDK:  # the real adapter hands the limit to the SDK as its budget
        ClaudeAgentOptions = dict
    monkeypatch.setattr(claude, "_load_sdk", lambda: SDK)
    assert uitest._default_tester(0.5, "claude-sonnet-5-5").max_budget_usd == 0.5


def test_a_tester_that_reaches_the_cap_stops_the_task_and_nothing_else_runs(proj, monkeypatch):
    checker = FakeChecker()
    tester = FakeTester(cost=5.0)  # far past what's left of the cap
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)]]))
    from parallax import pilot as p
    tid = p.intake(proj, "fix the README")["task"]
    status = build.run_mode(proj, tid, "pilot", FakeDrafter(flows_docs()), lambda left, s: ScriptedAgent(
        steps=[("write", "README.md", "ok\n")]), checker, test_runner=junit_runner(), preflight_runner=good_probe)
    assert status == "stuck" and checker.briefs == [] and not kinds(proj, "flows.recorded", tid)
    [item] = proj.inbox()
    assert item["data"]["budget"] and "while Field was using the app" in item["reason"]


# behavior outside the page: never a flow (c08f9e: OS notifications failed every flow, rework to the cap) ------

NOTIFY = "2. inferred: A desktop notification appears when it's done, and a click on it opens the app. (not browser-testable)"
# outside the page, in words the draft check can't see, so they reach Field unmarked: the flow is what says it
UNMARKED = "2. inferred: You hear about it when it's done, even with the page in the background."
MIXED = "2. inferred: The page shows Done when it finishes, and you hear about it with the page in the background."


def untestable_docs(flows, outcome=NOTIFY):
    d = flows_docs()
    d["intent"] = d["intent"].replace("1. asked: A new user on WSL can follow them.",
                                      "1. asked: A new user on WSL can follow them.\n" + outcome)
    d["plan"] = d["plan"].replace('user_flows = ["1"]', f"user_flows = {json.dumps(flows)}").replace(
        'covers = { "1" = ["tests/test_readme.py"] }', 'covers = { "1" = ["tests/test_readme.py"], "2" = ["tests/test_readme.py"] }')
    return d


class TwoFlowTester(FakeTester):
    """Writes a flow for each outcome it's shown, and one for the notification whatever it's shown."""
    page = {"outcome": 1, "name": "opens", "works": True, "saw": "the page opens"}
    outside = {"outcome": 2, "name": "notify", "works": False, "saw": "no desktop notification appeared"}

    def run(self, goal, cwd, server, allowed):
        self.calls.append({"goal": goal})
        (Path(cwd) / uitest.APP_UP).touch()
        (Path(cwd) / "flows").mkdir()
        for flow in (self.page, self.outside):
            name = flow["name"]
            (Path(cwd) / "flows" / f"{name}.spec.js").write_text(SPEC.replace("'opens'", f"'{name}'"))
        reply = {"flows": [self.page, self.outside], "not_looked_at": "nothing"}
        return AgentResult("done", json.dumps(reply), 0.2)


class MixedTester(TwoFlowTester):
    """One outcome with two parts: the page's own Done, and the OS notification no browser can see."""
    page = {"outcome": 2, "name": "opens", "works": True, "saw": "the page shows Done when it finishes"}
    outside = {"outcome": 2, "name": "desktop-notification", "works": False, "saw": "no desktop notification appeared"}


def run_with(proj, docs_, tester, runner, monkeypatch):
    from parallax import pilot as p
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", runner)
    tid = p.intake(proj, "fix the README")["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    return tid, build.run_mode(proj, tid, "pilot", FakeDrafter(docs_), lambda left, s: maker, FakeChecker(),
                               test_runner=junit_runner(), preflight_runner=good_probe)


def test_an_outcome_outside_the_page_is_never_a_flow_and_is_named_under_not_looked_at(proj, monkeypatch):
    tester = TwoFlowTester()
    tid, status = run_with(proj, untestable_docs(["1", "2"]), tester, flow_runner([[("opens", True)]]), monkeypatch)
    assert status == "ready"  # no flow that could never pass, so no rework toward it
    goal = tester.calls[0]["goal"]
    assert "A new user on WSL can follow them." in goal and "desktop notification" not in goal
    assert "can't test in a browser" in goal  # its own rule, for parts it finds outside the page
    [rec] = kinds(proj, "uitest.recorded", tid)
    assert list(rec["data"]["files"]) == [f"docs/tasks/{tid}/ui_flows/opens.spec.js"]  # the notification's flow dropped
    assert [f["name"] for f in rec["data"]["flows"]] == ["opens"]
    assert rec["data"]["not_looked_at"] == ("can't test in a browser: outcome 2 (A desktop notification appears when it's "
                                            "done, and a click on it opens the app)")
    assert "can't test in a browser: outcome 2" in show.report(proj, tid)


def test_a_plan_whose_only_flows_are_outside_the_page_never_starts_field(proj, monkeypatch):
    tid, status = run_with(proj, untestable_docs(["2"]), None, None, monkeypatch)  # a tester here would fail the test
    assert status == "ready" and not kinds(proj, "uitest.started", tid)


def test_focus_marks_outcomes_a_browser_cant_test_and_the_plan_leaves_them_out():
    from parallax import lifecycle
    intent = "## Outcome\n1. asked: a\n" + NOTIFY + "\n3. inferred: c (Not Browser-Testable).\n\n## Constraints\nnone\n"
    assert lint.browser_untestable(intent) == {"2", "3"}
    assert "(not browser-testable)" in lifecycle.SHAPES["intent"] and "Never list an outcome marked" in lifecycle.SHAPES["plan"]


def test_an_unmarked_notification_outcome_gets_no_flow(proj, monkeypatch):
    """Focus missed the marker, so the flow itself is what says it: an OS notification, never a test."""
    tid, status = run_with(proj, untestable_docs(["1", "2"], UNMARKED), TwoFlowTester(),
                           flow_runner([[("opens", True)]]), monkeypatch)
    assert status == "ready"  # the notification's flow can only fail, so it's no rework of the maker's
    [rec] = kinds(proj, "uitest.recorded", tid)
    assert list(rec["data"]["files"]) == [f"docs/tasks/{tid}/ui_flows/opens.spec.js"]
    assert [f["name"] for f in rec["data"]["flows"]] == ["opens"]
    assert rec["data"]["not_looked_at"] == "can't test in a browser: outcome 2 (no desktop notification appeared)"
    assert "can't test in a browser: outcome 2" in show.report(proj, tid)


def test_a_mixed_outcome_keeps_only_its_page_flow(proj, monkeypatch):
    """One outcome, a page part and an outside part: the page part is tested, the rest is named."""
    tid, status = run_with(proj, untestable_docs(["2"], MIXED), MixedTester(),
                           flow_runner([[("opens", True)]]), monkeypatch)
    assert status == "ready"
    [rec] = kinds(proj, "uitest.recorded", tid)
    assert list(rec["data"]["files"]) == [f"docs/tasks/{tid}/ui_flows/opens.spec.js"]  # the page part, tested
    assert [f["saw"] for f in rec["data"]["flows"]] == ["the page shows Done when it finishes"]
    assert rec["data"]["not_looked_at"] == "can't test in a browser: outcome 2 (no desktop notification appeared)"


def test_when_every_flow_is_outside_the_page_the_human_hears_why(proj, monkeypatch):
    """Nothing is kept, so the task can't go on: the reason says what it wrote, not "wrote no tests"."""
    class OutsideOnly(FakeTester):
        def run(self, goal, cwd, server, allowed):
            self.calls.append({"goal": goal})
            (Path(cwd) / uitest.APP_UP).touch()
            (Path(cwd) / "flows").mkdir()
            (Path(cwd) / "flows" / "notify.spec.js").write_text(SPEC.replace("'opens'", "'notify'"))
            reply = {"flows": [{"outcome": 2, "name": "notify", "works": False, "saw": "no desktop notification appeared"}],
                     "not_looked_at": "nothing"}
            return AgentResult("done", json.dumps(reply), 0.2)
    tid, status = run_with(proj, untestable_docs(["2"], UNMARKED), OutsideOnly(), None, monkeypatch)
    assert status == "disputed" and not kinds(proj, "uitest.recorded", tid)
    reason = kinds(proj, "disagreement.raised", tid)[-1]["reason"]
    assert "every flow Field wrote was for behavior outside the browser" in reason and "outcome 2" in reason


def test_outside_page_terms_match_os_behavior_only():
    for text in ("a desktop notification appears", "an OS notification on completion", "a push notification",
                 "the permission prompt for the camera", "a file dialog opens", "it uses the file picker",
                 "an icon in the system tray", "no desktop-notification appeared"):
        assert lint.outside_page_terms(text), text
    for text in ("a toast says Saved", "an alert banner appears", "the notifications page lists them",
                 "a dialog asks you to confirm", "the page opens"):  # in the page, so testable
        assert lint.outside_page_terms(text) == [], text
    assert lint.outside_page_terms("no desktop-notification, and no file dialog") == ["desktop notification", "file dialog"]


def test_a_prompt_tells_the_tester_to_split_mixed_outcomes():
    from parallax import lifecycle
    said = " ".join(uitest.PROMPT.split())  # the rule reads across the prompt's wrapped lines
    assert "One outcome can have both a page part and an outside part" in said
    assert "write a flow for only the page part" in said and "A flow's name and its saw describe only what the page shows" in said
    assert 'name it in not_looked_at as "can\'t test in a browser: <what>"' in said
    assert "mixes page behavior with behavior outside it" in lifecycle.SHAPES["intent"]
