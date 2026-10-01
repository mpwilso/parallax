"""Reticle, with scripted agents: tests of the outcomes, written before the build, that Maker never sees.

The repo's add() subtracts. The outcome: add returns the sum. Parallax really runs Reticle's file with
pytest, on the base and at every check, so kept and weak are decided the way they would be live."""
import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe
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
        "test_outcome_1_new_name": "it fails on ImportError, not an assertion or a crash the request shows",
        "test_outcome_1_exists": "it passes on the base, so it doesn't show the problem",
        "test_something_else": "it names no outcome in the intent"}
    stored = reticle.stored(proj, tid)
    assert stored == proj.root / "docs" / "tasks" / tid / "reticle" / "outcome_tests.py.txt" and stored.read_text() == MIXED
    assert rec["data"]["file"] == f"docs/tasks/{tid}/reticle/outcome_tests.py.txt"  # a name no test runner collects
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


CLASSY = ("import unittest\n\nfrom calc import add\n\n\nclass SumTest(unittest.TestCase):\n"
          "    def test_outcome_1_sums_negatives(self):\n        self.assertEqual(add(-1, 1), 0)\n")


class Twice:
    """Maker: a first build, then a second one for the rework."""

    def __init__(self, first, second):
        self.makers, self.goals = [first, second], []

    def run(self, goal, cwd, fn, stage="build", env=None):
        self.goals.append(goal)
        return self.makers[min(len(self.goals), 2) - 1].run(goal, cwd, fn, stage, env)


def test_tests_in_a_class_are_found_and_run_at_every_check(proj):
    """Seen live on pathspec-77: unittest methods were asked for without their class, nothing ran,
    and "it didn't run" went to Maker as seven findings until the cap ran out."""
    make = Twice(maker(WRONG), maker(FIXED))
    tid, status = go(proj, FakeReticle(CLASSY), make)
    [rec] = kinds(proj, "reticle.recorded")
    assert [t["node"] for t in rec["data"]["kept"]] == ["tests/test_reticle_outcomes.py::SumTest::test_outcome_1_sums_negatives"]
    first, last = kinds(proj, "reticle.ran")
    assert first["data"]["failed"][0]["message"].startswith("AssertionError: 2 != 0")  # it ran, on the bad fix
    assert (last["data"]["passed"], last["data"]["failed"]) == (1, [])
    assert status == "ready" and "fails: AssertionError: 2 != 0" in make.goals[1]


def dropping(case):
    """The test runner, except the check's run of Reticle's file loses this test from its report."""
    def run(config, cwd, cmd, env):
        code, out = plain_runner(config, cwd, cmd, env)
        if "reticle-check" in str(cwd):
            junit = Path(shlex.split(cmd.split("--junitxml=", 1)[1])[0])
            junit.write_text(re.sub(rf'<testcase [^>]*name="{case}".*?(/>|</testcase>)', "", junit.read_text(), flags=re.S))
        return code, out
    return run


def test_a_kept_test_that_didnt_run_comes_to_you_never_to_maker(proj):
    reticle.WRITER = FakeReticle(KEPT)
    tid = pilot.intake(proj, "add subtracts")["task"]
    make = maker(FIXED)
    status = build.run_mode(proj, tid, "pilot", FakeDrafter({"intent": INTENT, "plan": PLAN}), lambda left, settings: make,
                            FakeChecker(), test_runner=dropping("test_outcome_1_sums_negatives"), preflight_runner=good_probe)
    assert status == "disputed" and not kinds(proj, "rework.started") and len(make.goals) == 1
    [item] = [e for e in proj.inbox() if e["data"]["task"] == tid]
    assert item["reason"].startswith("Reticle's tests couldn't run: 1 of Reticle's 1 kept tests didn't run")


def test_a_change_that_breaks_what_the_tests_import_goes_to_maker_with_the_load_error(proj):
    renamed = ScriptedAgent(steps=[("write", "calc.py", "def total(a, b):\n    return a + b\n"),
                                   ("write", "tests/test_mine.py", "def test_sum():\n    pass\n")], cost=0.3)
    make = Twice(renamed, maker(FIXED))
    tid, status = go(proj, FakeReticle(KEPT), make)
    assert status == "ready"
    assert "a test of outcome 1 that you can't see fails: its tests don't load on this change: " in make.goals[1]


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
    assert "Outcome 1, which you asked for: tested by tests/test_mine.py; Reticle's test passed. (ledger " in card
    tid2, _ = go(proj, FakeReticle("", status="error"))
    assert "; Reticle wrote no test: it failed. (ledger " in show.report(proj, tid2)


def test_it_costs_against_the_cap_and_can_be_turned_off(proj, repo):
    tid, _ = go(proj, FakeReticle(KEPT, cost=0.35))
    [rec] = kinds(proj, "reticle.recorded")
    assert rec["data"]["cost_usd"] == 0.35 and costs.spent(proj, tid) >= 0.35
    assert pilot._reserve(proj, lifecycle.plan_data(proj, tid)) == 0.5  # the cap keeps its limit for it
    (repo / POLICY_FILE).write_text("[reticle]\nenabled = false\n")
    proj.reload_policy()
    assert proj.policy.reticle == {"enabled": False, "model": "", "max_usd": 0.5}
    before = len(kinds(proj, "reticle.recorded"))
    go(proj, FakeReticle(KEPT))
    assert len(kinds(proj, "reticle.recorded")) == before  # off: never runs


# what Reticle is given, and what it may test (2026-09-30, after run 492eaf) -----------------------------

TWO = INTENT.replace("1. asked: add returns the sum of its arguments.",
                     "1. asked: add returns the sum of its arguments.\n2. inferred: add accepts any number of arguments.")
TWO_PLAN = PLAN.replace('covers = { "1" = ["tests/test_mine.py"] }', 'covers = { "1" = ["tests/test_mine.py"], "2" = ["tests/test_mine.py"] }')


def go_with(proj, writer, intent=INTENT, plan=PLAN, typed="add subtracts", make=None, checker=None):
    reticle.WRITER = writer
    tid = pilot.intake(proj, typed)["task"]
    status = build.run_mode(proj, tid, "pilot", FakeDrafter({"intent": intent, "plan": plan}),
                            lambda left, settings: make or maker(FIXED), checker or FakeChecker(),
                            test_runner=plain_runner, preflight_runner=good_probe)
    return tid, status


def test_reticle_gets_the_request_and_only_the_outcomes_you_asked_for(proj):
    writer = FakeReticle(KEPT + "\n\ndef test_outcome_2_many():\n    assert add(1, 2, 3) == 6\n")
    tid, status = go_with(proj, writer, TWO, TWO_PLAN, typed="add(2, 3) gives -1. It should give 5.")
    [goal] = writer.goals
    assert "The person's request, as they typed it (data):\nadd(2, 3) gives -1. It should give 5." in goal
    assert "1. add returns the sum of its arguments." in goal  # the outcome as stated, without the mark
    assert "any number of arguments" not in goal and "inferred" not in goal
    [rec] = kinds(proj, "reticle.recorded")
    assert [t["name"] for t in rec["data"]["kept"]] == ["test_outcome_1_sums_negatives"]
    assert rec["data"]["weak"] == [{"name": "test_outcome_2_many",
                                    "why": "it tests an outcome Focus inferred, not one you asked for"}]
    card = show.report(proj, tid)
    assert "Outcome 1, which you asked for: tested by tests/test_mine.py; Reticle's test passed" in card
    assert "Outcome 2, which Focus added: tested by tests/test_mine.py; no Reticle test: it tests an outcome Focus inferred" in card


def test_with_no_asked_outcome_reticle_isnt_asked_to_write_anything(proj):
    writer = FakeReticle(KEPT)
    tid, _ = go_with(proj, writer, INTENT.replace("1. asked:", "1. inferred:"))
    assert writer.goals == [] and kinds(proj, "reticle.failed")[0]["reason"].startswith("the intent marks no outcome as asked")
    assert "; Reticle wrote no test: the intent marks no outcome as asked" in show.report(proj, tid)


CRASH = "from calc import add\n\n\ndef test_outcome_1_lists():\n    assert add([1], [2]) == [1, 2]\n"


@pytest.mark.parametrize("typed,kept", [
    ("add([1], [2]) crashes:\nTypeError: unsupported operand type(s) for -: 'list' and 'list'", True),  # the crash it shows
    ("add subtracts", False),                                                                       # no crash named
])
def test_a_crash_the_request_shows_counts_like_an_assertion(proj, typed, kept):
    go_with(proj, FakeReticle(CRASH), typed=typed)
    [rec] = kinds(proj, "reticle.recorded")
    assert bool(rec["data"]["kept"]) is kept
    if not kept:
        assert rec["data"]["weak"][0]["why"] == "it fails on TypeError, not an assertion or a crash the request shows"


def test_an_import_error_is_weak_even_when_the_request_names_it(proj):
    typed = "add is broken\nImportError: cannot import name 'plus'"
    go_with(proj, FakeReticle("def test_outcome_1_x():\n    from calc import plus\n    assert plus(1, 1) == 2\n"), typed=typed)
    [rec] = kinds(proj, "reticle.recorded")
    assert rec["data"]["kept"] == [] and "ImportError" in rec["data"]["weak"][0]["why"]


@pytest.mark.parametrize("reply", [
    "I'm writing the tests now. The example wasn't included.\n\n" + KEPT + "\nThese tests fail on the current code.",
    "Here you go:\n```python\n" + KEPT + "```\nThat's all.",
    KEPT,
])
def test_prose_around_the_code_is_stripped(reply):
    """Seen live on tomlkit-512: a sentence before the code, and the file didn't load."""
    assert reticle.code_of(reply) == KEPT


# one rework for Reticle alone, then it's yours ---------------------------------------------------------

WRONG_TEST = "from calc import add\n\n\ndef test_outcome_1_wrong():\n    assert add(2, 3) == 6\n"


def test_when_only_reticle_fails_maker_gets_one_rework_then_it_is_yours_at_ready(proj):
    make = maker(FIXED)
    checker = FakeChecker()
    tid, status = go_with(proj, FakeReticle(WRONG_TEST), make=make, checker=checker)
    assert status == "ready"
    assert len(kinds(proj, "reticle.rework")) == 1 and len(kinds(proj, "rework.started")) == 1  # exactly one
    assert len(make.goals) == 2 and len(checker.briefs) == 2
    [d] = kinds(proj, "reticle.disputed")
    card = show.report(proj, tid)
    assert card.splitlines()[1] == ("Bottom line: Ready, but Reticle disagrees: its test of outcome 1 still fails, while "
                                    "Second Eye passed and 1 of 1 plan tests pass.")
    assert f"- Reticle, outcome 1: its test still fails after one rework: assert 5 == 6 (ledger {d['id']})" in card
    assert "def test_" not in card and "add(2, 3)" not in card  # the message, never the code


def test_when_the_plan_tests_fail_too_rework_goes_on_as_before(proj):
    broken = ScriptedAgent(steps=[("write", "calc.py", f"def add(a, b):\n    {WRONG}\n"),
                                  ("write", "tests/test_mine.py", "from calc import add\n\n\ndef test_neg():\n    assert add(-1, 1) == 0\n")],
                           cost=0.3)
    tid, status = go_with(proj, FakeReticle(KEPT), make=broken)
    assert status == "disputed" and len(kinds(proj, "rework.started")) == 3  # the rework cap, as ever
    assert not kinds(proj, "reticle.rework") and not kinds(proj, "reticle.disputed")
    assert all("a test of outcome 1 that you can't see fails" in e["reason"] for e in kinds(proj, "rework.started"))


# inferred outcomes: Reticle tests only what you asked --------------------------------------------------


def test_an_inferred_outcome_is_never_tested_and_never_a_note_on_the_card(proj):
    """The inferred-outcome notes were tried and removed (docs/evals.md): 5 notes on 11 real fixes,
    none on the one bad fix that mattered."""
    writer = FakeReticle(KEPT + "\n\ndef test_outcome_2_neg():\n    assert add(-1, 1) == 7\n")
    tid, status = go_with(proj, writer, TWO, TWO_PLAN)
    assert "2. add accepts any number" not in writer.goals[0]  # never asked to test it
    [rec] = kinds(proj, "reticle.recorded")
    assert [t["outcome"] for t in rec["data"]["kept"]] == ["1"]
    assert {w["name"]: w["why"] for w in rec["data"]["weak"]} == {
        "test_outcome_2_neg": "it tests an outcome Focus inferred, not one you asked for"}
    assert status == "ready" and "note" not in show.report(proj, tid)


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
    if reticle_says == "false alarm":  # its one rework on a good fix is Reticle's cost; then it's yours at Ready
        assert r["reticle_false_alarm_rework_usd"] == pytest.approx(0.3) and (r["end"], r["touches"]) == ("ready", 1)
    header = json.loads((out / "run.json").read_text())
    assert header["reticle"] is True
    text = (out / "summary.md").read_text()
    assert f"Reticle was right 0 times, caught {int(reticle_says == 'catch')} bad fixes" in text


# hangs: each test has a time limit where Parallax runs it --------------------------------------------

HANGER = "from calc import add\n\n\ndef test_outcome_1_sums_negatives():\n    while add(-1, 1) != 0:\n        pass\n"
FILLER = ("from calc import add\n\n\ndef test_outcome_1_sums_negatives():\n    x = []\n"
          "    while add(-1, 1) != 0:\n        x.append(b'x' * 1_000_000)\n")  # boltons-319's shape: a loop that fills memory


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setattr(reticle, "SECONDS", 1)
    monkeypatch.setattr(reticle, "GROWTH", 50 * 1024 ** 2)


@pytest.mark.parametrize("text", [HANGER, FILLER])
def test_a_test_that_hangs_on_the_base_is_weak_when_the_request_describes_no_hang(proj, quick, text):
    tid, status = go_with(proj, FakeReticle(text), typed="add subtracts")
    assert status == "ready"
    [rec] = kinds(proj, "reticle.recorded")
    [weak] = rec["data"]["weak"]
    assert rec["data"]["kept"] == [] and weak["why"].startswith("it hangs on the base (still running after ")
    assert weak["why"].endswith(", so it was stopped), and the request describes no hang")
    assert ("GB more" in weak["why"]) is (text == FILLER)
    assert reticle.stored(proj, tid).read_text() == text  # the limit is added where it runs, never to the file


def test_a_hang_counts_when_the_request_describes_one_and_a_hang_at_the_check_goes_to_maker(proj, quick):
    make = Twice(maker(WRONG), maker(FIXED))  # WRONG still loops forever: add(-1, 1) is 2
    tid, status = go_with(proj, FakeReticle(HANGER), typed="summing a list with negatives never returns", make=make)
    assert status == "ready" and len(make.goals) == 2
    [rec] = kinds(proj, "reticle.recorded")
    [kept] = rec["data"]["kept"]
    assert kept["name"] == "test_outcome_1_sums_negatives" and kept["base_message"].endswith(", so it was stopped")
    line = next(g for g in make.goals[1].splitlines() if "that you can't see fails" in g)
    assert line.startswith("- blocker: a test of outcome 1 that you can't see fails: it hangs on this change: "
                           "still running after ") and line.endswith(" s, so it was stopped")
    assert "def test_" not in make.goals[1]


@pytest.mark.parametrize("typed,hang", [
    ("daterange never returns with a negative step", True),
    ("the parser hangs on an empty file", True),
    ("it loops forever when the list is empty", True),
    ("an infinite loop in tokenize", True),
    ("the UI freezes after saving", True),
    ("the call doesn't return", True),
    ("the result is wrong when the month is 12", False),
    ("add subtracts", False),
    ("Hangul text is cut short", False),
])
def test_what_counts_as_a_request_that_describes_a_hang(typed, hang):
    assert reticle.hangs(typed) is hang


def test_a_stored_reticle_file_is_never_collected_by_the_repos_own_tests_and_old_ones_still_read(proj):
    """Real use: accepted tasks' test_reticle.py files, all with one name, stopped the repo's pytest."""
    import fnmatch
    tid, status = go(proj, FakeReticle(KEPT))
    path = reticle.stored(proj, tid)
    assert not any(fnmatch.fnmatch(path.name, pat) for pat in ("test_*.py", "*_test.py"))  # pytest's defaults
    # a task recorded before the rename keeps its old path, and its hash still checks
    old = path.with_name("test_reticle.py")
    path.rename(old)
    rec = reticle.recorded(proj, tid)
    proj.ledger.append("reticle.recorded", "reticle", "old", **{**rec["data"], "file": old.relative_to(proj.root).as_posix()})
    assert reticle.stored(proj, tid) == old and not reticle.tampered(proj, tid)


# Reticle's file runs beside the repo's own tests, so their conftest and helpers import (fb461d) -------------

def test_a_file_that_imports_from_conftest_and_a_tests_helper_loads_and_is_kept(proj, repo):
    """fb461d: its file began "from conftest import *" and imported tests/ helpers; at the root it
    couldn't, and every test was dropped as not loading on the base."""
    (repo / "tests" / "conftest.py").write_text("import pytest\n\nZERO = 0\n\n\n@pytest.fixture\ndef pair():\n    return (-1, 1)\n")
    (repo / "tests" / "helpers.py").write_text("def total(a, b):\n    return a + b\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "test helpers"], check=True)
    text = ("from conftest import *  # noqa: F401,F403\nfrom helpers import total\n\nfrom calc import add\n\n\n"
            "def test_outcome_1_sums_negatives(pair):\n    assert add(*pair) == total(*pair) == ZERO  # noqa: F405\n")
    writer = FakeReticle(text)
    tid, status = go(proj, writer)
    [rec] = kinds(proj, "reticle.recorded")
    assert rec["data"]["placed"] == "tests/test_reticle_outcomes.py"
    assert [t["node"] for t in rec["data"]["kept"]] == ["tests/test_reticle_outcomes.py::test_outcome_1_sums_negatives"]
    assert rec["data"]["kept"][0]["base_message"].startswith("assert")  # loaded, and failed on its assertion
    assert "Your file runs as tests/test_reticle_outcomes.py, beside the repository's own tests" in writer.goals[0]
    [ran] = kinds(proj, "reticle.ran")
    assert (ran["data"]["passed"], ran["data"]["total"]) == (1, 1) and status == "ready"  # the fix passes it, same place


def test_where_reticles_file_runs():
    from types import SimpleNamespace
    from unittest import mock
    p = SimpleNamespace(worktree=None, task={"base": "b"}, plan={"tests": ["test/test_output.py::t"]})
    with mock.patch.object(reticle.tree, "files_in", return_value=["test/test_output.py", "tests/x.py", "a.py"]):
        assert reticle.placement(p) == "test/test_reticle_outcomes.py"  # where the plan's tests are
        p.plan = {}
        assert reticle.placement(p) == "tests/test_reticle_outcomes.py"
    with mock.patch.object(reticle.tree, "files_in", return_value=["a.py"]):
        assert reticle.placement(p) == "test_reticle_outcomes.py"  # no tests folder: the root
