"""Reticle, with scripted agents: tests of the outcomes, written before the build, that Maker never sees.

The repo's add() subtracts. The outcome: add returns the sum. Parallax really runs Reticle's file with
pytest, on the base and at every check, so kept and weak are decided the way they would be live."""
import json
import subprocess

import pytest

from fakes import FakeChecker, FakeDrafter, good_probe
from parallax import build, check, costs, evals, lifecycle, pilot, reticle, show
from parallax.agents.base import AgentResult
from parallax.core import POLICY_FILE, Project
from test_evals import BUGGY, FIXED, INTENT, PLAN, WRONG, case_for, maker, plain_runner, result, scripted, upstream  # noqa: F401
from test_lifecycle_gates import make_key

KEPT = "from calc import add\n\n\ndef test_outcome_1_sums_negatives():\n    assert add(-1, 1) == 0\n"
MIXED = KEPT + (
    "\n\ndef test_outcome_1_new_name():\n    from calc import plus\n    assert plus(2, 3) == 5\n"  # an import, not an assertion
    "\n\ndef test_outcome_1_exists():\n    assert callable(add)\n"                               # passes on the base
    "\n\ndef test_something_else():\n    assert add(2, 3) == 5\n")                              # names no outcome


class FakeReticle:
    """Returns one test file; notes what it was given and where it could look."""

    def __init__(self, text, cost=0.2, status="done"):
        self.text, self.cost, self.status = text, cost, status
        self.goals, self.seen, self.limits = [], [], []

    def __call__(self, limit, model):
        self.limits.append((limit, model))
        return self

    def run(self, goal, cwd, permission_fn, stage="build", env=None):
        assert stage == "reticle"
        self.goals.append(goal)
        self.seen.append(sorted(p.relative_to(cwd).as_posix() for p in cwd.rglob("*") if p.is_file()))
        return AgentResult(self.status, self.text, self.cost)


@pytest.fixture
def proj(repo, monkeypatch):
    (repo / "calc.py").write_text(f"def add(a, b):\n    {BUGGY}\n")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text("from calc import add\n\n\ndef test_exists():\n    assert callable(add)\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "calc"], check=True)
    (repo / POLICY_FILE).write_text("[reticle]\nenabled = true\n")
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    return Project.init(repo)


def go(proj, writer, make=None, checker=None):
    reticle.WRITER = writer  # the conftest guard's monkeypatch puts the refusal back after the test
    tid = pilot.intake(proj, "add subtracts")["task"]
    status = build.run_mode(proj, tid, "pilot", FakeDrafter({"intent": INTENT, "plan": PLAN}),
                            lambda left, settings: make or maker(FIXED), checker or FakeChecker(),
                            test_runner=plain_runner, preflight_runner=good_probe)
    return tid, status


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


def test_only_tests_that_fail_on_an_assertion_at_the_base_are_kept(proj):
    writer = FakeReticle(MIXED)
    tid, status = go(proj, writer)
    assert status == "ready"
    [rec] = kinds(proj, "reticle.recorded")
    assert [t["name"] for t in rec["data"]["kept"]] == ["test_outcome_1_sums_negatives"]
    assert rec["data"]["kept"][0]["outcome"] == "1" and "assert" in rec["data"]["kept"][0]["base_message"]
    assert {w["name"]: w["why"] for w in rec["data"]["weak"]} == {
        "test_outcome_1_new_name": "it fails on ImportError, not an assertion",
        "test_outcome_1_exists": "it passes on the base, so it doesn't show the problem",
        "test_something_else": "it names no outcome in the intent"}
    stored = reticle.stored(proj, tid)
    assert stored == proj.root / "docs" / "tasks" / tid / "reticle" / "test_reticle.py" and stored.read_text() == MIXED
    assert rec["data"]["file"] == f"docs/tasks/{tid}/reticle/test_reticle.py"
    [ran] = kinds(proj, "reticle.ran")
    assert (ran["data"]["passed"], ran["data"]["total"], ran["data"]["failed"]) == (1, 1, [])  # the fix passes it


def test_a_file_that_doesnt_load_on_the_base_keeps_nothing_and_the_build_goes_on(proj):
    tid, status = go(proj, FakeReticle("from calc import nothing_here\n\n\ndef test_outcome_1_a():\n    assert False\n"))
    assert status == "ready"  # without tests, it builds and checks as before
    [rec] = kinds(proj, "reticle.recorded")
    assert rec["data"]["kept"] == [] and "doesn't load on the base" in rec["data"]["weak"][0]["why"]
    assert not kinds(proj, "reticle.ran")
    card = show.report(proj, tid)
    assert "; no Reticle test: the file doesn't load on the base" in card


def test_a_failure_goes_back_to_maker_as_the_outcome_and_message_never_the_code(proj):
    goals = []
    first, second = maker(WRONG), maker(FIXED)

    class Maker:
        def run(self, goal, cwd, fn, stage="build", env=None):
            goals.append(goal)
            return (first if len(goals) == 1 else second).run(goal, cwd, fn, stage, env)
    checker = FakeChecker()
    tid, status = go(proj, FakeReticle(KEPT), Maker(), checker)
    assert status == "ready" and len(goals) == 2
    [rework] = kinds(proj, "rework.started")
    assert "- blocker: a test of outcome 1 that you can't see fails: " in goals[1] and "assert 2 == 0" in goals[1]
    for g in goals:
        assert "def test_" not in g and "add(-1, 1)" not in g and "test_reticle" not in g
    assert all("that you can't see" not in b and "sums_negatives" not in b for b in checker.briefs)  # Second Eye's input as ever
    assert len(checker.briefs) == 2  # it judged both trees, so the eval can tell the two apart


def test_maker_never_receives_the_tests_and_reticle_never_sees_the_plan(proj):
    seen = []

    def look(cwd):  # during the build, in Maker's worktree
        seen.append(sorted(p.name for p in cwd.rglob("*") if p.is_file() and ".git" not in p.parts))
    writer = FakeReticle(KEPT)
    make = maker(FIXED, steps=[("call", look)])
    tid, status = go(proj, writer, make)
    assert status == "ready"
    assert "test_reticle.py" not in seen[0]
    assert all("sums_negatives" not in g for _, g in make.goals) and all("sums_negatives" not in str(e) for e in make.envs)
    [goal] = writer.goals
    assert "add returns the sum of its arguments" in goal and "Keep the function's name." in goal
    assert "## Steps" not in goal and "files =" not in goal and "add subtracts." not in goal  # no plan, no problem narrative
    assert writer.seen[0] == ["calc.py", "tests/test_calc.py"]  # a copy of the base, nothing else
    assert writer.limits[0][1] == proj.policy.draft["model"]  # the drafters' model by default


def test_a_changed_hash_stops_the_check(proj):
    tid, status = go(proj, FakeReticle(KEPT))
    assert status == "ready"
    stored = reticle.stored(proj, tid)
    stored.write_text(stored.read_text().replace("== 0", "== 0 or True"))
    assert check.check_once(proj, tid, lambda left, model: FakeChecker(), plain_runner)[0] == "disputed"
    [item] = [e for e in proj.inbox() if e["data"]["task"] == tid]
    assert item["reason"] == "Reticle's tests changed after they were recorded" and item["data"]["stage"] == "guard"


def test_the_card_says_per_outcome_what_reticle_found(proj):
    tid, _ = go(proj, FakeReticle(KEPT))
    card = show.report(proj, tid)
    assert "outcome 1: tests/test_mine.py; Reticle's test passed (ledger " in card
    tid2, _ = go(proj, FakeReticle("", status="error"))
    assert "; Reticle wrote no test: it failed (ledger " in show.report(proj, tid2)


def test_it_costs_against_the_cap_and_is_off_by_default(proj, repo):
    tid, _ = go(proj, FakeReticle(KEPT, cost=0.35))
    [rec] = kinds(proj, "reticle.recorded")
    assert rec["data"]["cost_usd"] == 0.35 and costs.spent(proj, tid) >= 0.35
    assert pilot._reserve(proj, lifecycle.plan_data(proj, tid)) == 0.5  # the cap keeps its limit for it
    (repo / POLICY_FILE).write_text("")
    proj.reload_policy()
    assert proj.policy.reticle == {"enabled": False, "model": "", "max_usd": 0.5}
    before = len(kinds(proj, "reticle.recorded"))
    go(proj, FakeReticle(KEPT))
    assert len(kinds(proj, "reticle.recorded")) == before  # off: never runs


# scored in the eval ---------------------------------------------------------------------------------

@pytest.mark.parametrize("body,test,reticle_says,second_eye", [
    (WRONG, KEPT, "catch", "miss"),  # add(-1, 1) is 2 with abs(): Reticle's test fails on the bad fix
    (WRONG, "from calc import add\n\n\ndef test_outcome_1_sum():\n    assert add(2, 3) == 5\n", "miss", "miss"),
    (FIXED, "from calc import add\n\n\ndef test_outcome_1_wrong():\n    assert add(2, 3) == 6\n", "false alarm", "right"),
])
def test_the_eval_scores_reticle_against_the_hidden_tests(repo, upstream, monkeypatch, body, test, reticle_says, second_eye):  # noqa: F811  upstream: test_evals' fixture
    make_key()
    proj = Project.init(repo)
    monkeypatch.setattr(reticle, "WRITER", FakeReticle(test, cost=0.2))
    out = evals.run(proj, [case_for(upstream)], 20.0, 4.0, pipeline=scripted(maker(body), FakeChecker()),
                    runner=plain_runner, say=lambda s: None, reticle=True)
    r = result(out)
    assert r["reticle"] == reticle_says and r["second_eye"] == second_eye
    assert r["reticle_kept"] == 1 and r["reticle_cost_usd"] == 0.2
    if reticle_says == "false alarm":  # it sent a good fix back three times: that Maker cost is Reticle's
        assert r["reticle_false_alarm_rework_usd"] == pytest.approx(0.9) and r["end"] == "needs you"
    header = json.loads((out / "run.json").read_text())
    assert header["reticle"] is True
    text = (out / "summary.md").read_text()
    assert f"Reticle was right 0 times, caught {int(reticle_says == 'catch')} bad fixes" in text
