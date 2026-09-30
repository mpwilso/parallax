"""The eval harness, end to end, with scripted agents: no model is ever called.

A tiny upstream repo stands in for a real project: at its base, add() subtracts; its fix makes
add() add, and adds a test the agents must never see. Parallax runs that test itself afterwards."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, blocker, good_probe
from parallax import build, evals, lint, pilot, review, stats
from parallax.agents import claude
from parallax.agents.base import Review
from parallax.cli import main
from parallax.core import POLICY_FILE, Project
from parallax.policy import Policy
from test_lifecycle_gates import make_key

HIDDEN = "HIDDEN-MAINTAINERS-TEST"
BUGGY, FIXED, WRONG = "return a - b", "return a + b", "return abs(a) + abs(b)"
INTENT = """\
Bottom line: Make add return the sum.
Not looked at: nothing

kind: bug
size: small
title: fixing add
scope: calc.py, tests/**

## Problem
add subtracts.

## Outcome
1. add returns the sum of its arguments.

## Constraints
Keep the function's name.
"""
PLAN = """\
Bottom line: Make add add, with a test.
Not looked at: nothing

## Steps
1. Fix calc.py.

## Tests
tests/test_mine.py checks a sum.

```toml
files = ["calc.py", "tests/test_mine.py"]
tests = ["tests/test_mine.py"]
lines_changed = 8
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = ""
estimated_cost_usd = 0.5
budget_cap_usd = 2.0
covers = { "1" = ["tests/test_mine.py"] }
```
"""


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """(repo, base, fix): a real merged fix, in miniature."""
    repo = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    old_test = "from calc import add\n\n\ndef test_exists():\n    assert callable(add)\n"
    base = _commit(repo, {"calc.py": f"def add(a, b):\n    {BUGGY}\n", "tests/test_calc.py": old_test}, "base")
    fix = _commit(repo, {"calc.py": f"def add(a, b):\n    {FIXED}\n",
                         "tests/test_calc.py": old_test + f"\n\ndef test_add():  # {HIDDEN}\n"
                                                          "    assert add(2, 3) == 5 and add(-1, 1) == 0\n"}, "fix")
    return repo, base, fix


def case_for(upstream, id="calc-1", tests=("tests/test_calc.py",)):
    repo, base, fix = upstream
    return evals.Case(id=id, repo=str(repo), base=base, fix=fix, tests=list(tests), setup=[],
                      goal="add() subtracts instead of adding.\n\n    add(2, 3) gives -1")


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def plain_runner(config, cwd, cmd, env):
    """The sandbox runner's stand-in: the same command, with this Python first on the PATH."""
    env = {**env, "PATH": f"{Path(sys.executable).parent}:{env['PATH']}"}
    out = subprocess.run(["bash", "-c", cmd], cwd=cwd, env=env, capture_output=True, text=True)
    return out.returncode, out.stdout + out.stderr


def maker(body, cost=0.3, steps=()):
    return ScriptedAgent(steps=[*steps, ("write", "calc.py", f"def add(a, b):\n    {body}\n"),
                                ("write", "tests/test_mine.py", "from calc import add\n\n\ndef test_sum():\n    assert add(2, 3) == 5\n")],
                         cost=cost)


def scripted(make, checker, drafter=None):
    """What `parallax do` runs, with scripted agents in place of models."""
    drafter = drafter or FakeDrafter({"intent": INTENT, "plan": PLAN}, cost=0.1)

    def pipeline(project, work):
        tid = pilot.intake(project, work, spawn=lambda *a: 4242)["task"]
        build.run_mode(project, tid, "pilot", drafter, lambda left, settings: make, checker,
                       test_runner=plain_runner, preflight_runner=good_probe)
        return tid
    pipeline.drafter = drafter
    return pipeline


def run(proj, cases, pipeline, budget=10.0, per_case=3.0):
    said = []
    out = evals.run(proj, cases, budget, per_case, pipeline=pipeline, runner=plain_runner, say=said.append)
    return out, said


def result(out, case_id="calc-1"):
    return json.loads((out / f"{case_id}.json").read_text())


# the four ways a case can be scored --------------------------------------------------------------

@pytest.mark.parametrize("body,review,end,hidden,judgment,touches", [
    (FIXED, Review("pass"), "ready", "pass", "right", 1),               # the fix passes the hidden tests
    (WRONG, Review("pass"), "ready", "fail", "miss", 1),                # it fails them, and Second Eye missed it
    (WRONG, blocker(where="calc.py:2"), "needs you", "fail", "catch", 2),  # Second Eye caught a bad fix
    (FIXED, blocker(where="calc.py:2"), "needs you", "pass", "false alarm", 2),  # and a false alarm
])
def test_each_case_is_scored_by_the_hidden_tests_and_second_eye_against_them(
        proj, upstream, body, review, end, hidden, judgment, touches):
    out, said = run(proj, [case_for(upstream)], scripted(maker(body), FakeChecker(reviews=[review])))
    r = result(out)
    assert (r["end"], r["hidden"], r["second_eye"], r["touches"]) == (end, hidden, judgment, touches)
    assert [v["judgment"] for v in r["verdicts"]] == [judgment]  # each tree judged once: rework left it the same
    assert r["cost_usd"] > 0 and r["seconds"] > 0
    assert (r["seconds_to_ready"] is not None) == (end == "ready")
    if end == "needs you":
        assert "the check still fails after 3 rework cycles" in r["decisions"][0]
    assert said[-1].startswith(f"[1/1] calc-1: {end}, hidden tests {hidden}, Second Eye {judgment}")

    header = json.loads((out / "run.json").read_text())
    assert header["done"] == ["calc-1"] and header["stopped"] is None
    assert header["fingerprint"]["Second Eye's model"] == proj.policy.check["model"]
    text = (out / "summary.md").read_text()
    assert lint.lint_report(text, root=proj.root) == []  # the output shape, with every Found cited
    passed = int(end == "ready" and hidden == "pass")  # a fix passes only if it reached Ready and passed
    assert text.splitlines()[1] == f"Bottom line: {passed} of 1 fix reached Ready and passed the hidden tests."
    assert text.splitlines()[2] == "Not looked at: nothing"  # Second Eye judged every case here
    assert ("0.0 min to Ready" in text) == (end == "ready")
    assert text.splitlines()[3] == ("Next: nothing needs you: every fix passed and Second Eye was right."
                                    if judgment == "right" else "Next: you read calc-1 in Details, then decide what to change.")
    (out / "summary.md").write_text("stale\n")
    assert evals.resummarize(proj.root, out).read_text() == text  # the same summary, from the committed JSON
    assert f"Second Eye was right {int(judgment == 'right')} times, caught {int(judgment == 'catch')}" in text


def test_a_catch_that_rework_fixes_counts_both_trees(proj, upstream):
    """Second Eye fails the first tree and passes the reworked one: a catch, then right."""
    first, second = maker(WRONG), maker(FIXED)
    calls = []

    class TwoTries:
        def run(self, *a, **k):
            calls.append(1)
            return (first if len(calls) == 1 else second).run(*a, **k)
    out, _ = run(proj, [case_for(upstream)], scripted(TwoTries(), FakeChecker(reviews=[blocker(where="calc.py:2"), Review("pass")])))
    r = result(out)
    assert [v["judgment"] for v in r["verdicts"]] == ["catch", "right"]
    assert (r["end"], r["hidden"], r["second_eye"]) == ("ready", "pass", "right")


def test_a_fix_nothing_reviewed_never_counts_as_passed_and_is_named(proj, upstream):
    """41250f: four builds stopped at their cap before the check. Their trees passed the hidden
    tests, and the summary counted them as passed with Not looked at: nothing."""
    cheap, dear = maker(FIXED), maker(FIXED, cost=2.5)  # $2.50 of build is past the $2.20 cap

    def pipeline(project, work):
        return scripted(dear if project.root.parent.name == "calc-2" else cheap, FakeChecker())(project, work)
    out, _ = run(proj, [case_for(upstream, "calc-1"), case_for(upstream, "calc-2")], pipeline, per_case=3.0)
    capped = result(out, "calc-2")
    assert (capped["end"], capped["hidden"], capped["verdicts"]) == ("needs you", "pass", [])
    text = (out / "summary.md").read_text()
    lines = text.splitlines()
    assert lines[1] == "Bottom line: 1 of 2 fixes reached Ready and passed the hidden tests."
    assert lines[2] == "Not looked at: see Found (1)"  # the reason, cited, is too long for the header
    assert "- calc-2 never reached the check: the budget cap ran out ($2.70 of $2.20 estimated); nothing reviewed its tree, " \
        "which passes the hidden tests (evals/results/" in text
    assert lines[3] == "Next: you read calc-2 in Details, then decide what to change."
    assert "calc-2: ended needs you, hidden tests pass on a tree nothing reviewed, Second Eye never judged it" in text
    assert lint.lint_report(text, root=proj.root) == []


def test_four_cases_that_never_reached_the_check_are_each_named_with_why(tmp_path):
    header = {"run": "r", "parallax": "p", "budget_usd": 15.0, "per_case_usd": 4.0, "spent_usd": 3.0,
              "cases": ["a", "b", "c", "d", "e"], "done": ["a", "b", "c", "d", "e"], "stopped": None}
    stuck = {"end": "needs you", "hidden": "pass", "verdicts": [], "cost_usd": 0.7, "seconds_to_ready": None, "touches": 2,
             "decisions": ["the budget cap ran out ($0.72 of $0.70 estimated)."]}
    results = [{"case": x, **stuck} for x in "abcd"] + [
        {"case": "e", "end": "ready", "hidden": "pass", "cost_usd": 0.4, "seconds_to_ready": 60.0, "touches": 1,
         "verdicts": [{"tree": "t", "verdict": "pass", "hidden": "pass", "judgment": "right"}]}]
    out = tmp_path / "evals" / "results" / "r"
    out.mkdir(parents=True)
    for r in results:
        (out / f"{r['case']}.json").write_text(json.dumps(r))
    (out / "run.json").write_text(json.dumps(header))
    text = evals.summary(tmp_path, out, header, results)
    assert text.splitlines()[1] == "Bottom line: 1 of 5 fixes reached Ready and passed the hidden tests."
    assert text.splitlines()[2] == "Not looked at: see Found (4)"  # too long for the header, so each is cited
    for x in "abcd":
        assert (f"- {x} never reached the check: the budget cap ran out ($0.72 of $0.70 estimated); nothing reviewed "
                f"its tree, which passes the hidden tests (evals/results/r/{x}.json:1)") in text
    assert lint.lint_report(text, root=tmp_path) == []


def test_progress_lines_are_out_as_they_happen(proj, upstream, monkeypatch):
    """By default every progress line is printed with a flush, so a file or a pipe gets it at once."""
    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append((a, k)))
    evals.run(proj, [case_for(upstream)], 1.0, 3.0, pipeline=scripted(maker(FIXED), FakeChecker()))  # say: the default
    assert printed == [(("stopped before calc-1: $0.00 spent, and the next case needs up to $3.30 (its $3.00 ceiling "
                         "plus 10%) of the $1.00 budget.",), {"flush": True})]


def test_both_committed_summaries_regenerate_from_their_json():
    """The committed runs' summaries are what the summary code writes from their JSON today."""
    root = Path(__file__).resolve().parents[1]
    for run_dir in sorted((root / evals.RESULTS_DIR).glob("*/run.json")):
        out = run_dir.parent
        header = json.loads(run_dir.read_text())
        results = [json.loads((out / f"{c}.json").read_text()) for c in header["done"]]
        assert (out / "summary.md").read_text() == evals.summary(root, out, header, results), out.name


# the hidden tests stay hidden ------------------------------------------------------------------------

def test_no_agent_ever_sees_the_hidden_tests(proj, upstream):
    seen = []

    def look(cwd):  # during the build, in the worktree Maker works in
        files = [p for p in Path(cwd).rglob("*") if p.is_file() and ".git" not in p.parts]
        seen.append(any(HIDDEN in p.read_text(errors="replace") for p in files))
        fix = upstream[2]
        seen.append(subprocess.run(["git", "-C", str(cwd), "cat-file", "-e", f"{fix}^{{commit}}"],
                                   capture_output=True).returncode == 0)
    checker = FakeChecker()
    pipeline = scripted(maker(FIXED, steps=[("call", look)]), checker)
    out, _ = run(proj, [case_for(upstream)], pipeline)
    assert seen == [False, False]  # not in the worktree, and the fix isn't in its history
    assert checker.briefs and not any(HIDDEN in b for b in checker.briefs)
    assert not any(HIDDEN in r for r in pipeline.drafter.requests)
    assert result(out)["hidden"] == "pass"  # and still, Parallax ran them afterwards


def test_the_scratch_copy_is_never_the_working_repo(proj, upstream):
    before = subprocess.run(["git", "-C", str(proj.root), "status", "--porcelain"], capture_output=True, text=True).stdout
    out, _ = run(proj, [case_for(upstream)], scripted(maker(FIXED), FakeChecker()))
    r = result(out)
    assert Path(r["scratch"]).is_relative_to(evals.home()) and not Path(r["scratch"]).is_relative_to(proj.root)
    assert [e["kind"] for e in proj.ledger.entries()] == ["project.init"]  # no task in your ledger
    after = subprocess.run(["git", "-C", str(proj.root), "status", "--porcelain"], capture_output=True, text=True).stdout
    assert set(after.splitlines()) - set(before.splitlines()) == {"?? evals/"}  # only the results


# the budget ----------------------------------------------------------------------------------------------

def test_the_run_stops_before_a_case_that_might_not_fit_and_says_what_it_finished(proj, upstream):
    cases = [case_for(upstream, "calc-1"), case_for(upstream, "calc-2")]
    out, said = run(proj, cases, scripted(maker(FIXED), FakeChecker()), budget=3.5, per_case=3.0)
    header = json.loads((out / "run.json").read_text())
    assert header["done"] == ["calc-1"] and header["stopped"]["case"] == "calc-2"
    assert header["spent_usd"] + 3.3 > 3.5 >= 3.3  # the first fit with its margin; the second wouldn't
    assert not (out / "calc-2.json").exists()
    assert said[-1].startswith("stopped before calc-2: $0.50 spent, and the next case needs up to $3.30")
    text = (out / "summary.md").read_text()
    assert "the budget stopped the run before calc-2" in text and "1 case the budget didn't reach" in text
    assert "Next: you rerun the rest with a larger budget." in text
    assert lint.lint_report(text, root=proj.root) == []

    out, said = run(proj, cases, scripted(maker(FIXED), FakeChecker()), budget=3.0, per_case=3.0)
    assert json.loads((out / "run.json").read_text())["done"] == [] and said == [
        "stopped before calc-1: $0.00 spent, and the next case needs up to $3.30 (its $3.00 ceiling plus 10%) of the $3.00 budget."]


def test_each_case_runs_hands_free_under_your_policy_and_its_ceiling(proj, upstream):
    text = evals.eval_policy(proj.policy, evals.Case(**{**case_for(upstream).__dict__, "setup": ["uv pip install -e . pytest"]}), 3.0)
    import tomllib
    p = Policy.from_dict(tomllib.loads(text))
    assert (p.budget["small_cap_usd"], p.launch["auto_launch_usd"], p.launch["review_plans"]) == (3.0, 3.0, False)
    assert p.budget["drafting_usd"] == 0.5 and not p.ui_tester["enabled"]  # six drafting calls fit the ceiling
    assert (p.check, p.draft, p.limits) == ({**proj.policy.check, "no_em_dashes": False}, proj.policy.draft, proj.policy.limits)
    mine = Policy.from_dict({"check": {"no_em_dashes": True}})  # Parallax's own style rule never reaches a case
    assert "no_em_dashes = false" in evals.eval_policy(mine, case_for(upstream), 3.0)
    assert p.build["setup"].startswith('uv venv -q --python /usr/bin/python3 "$PARALLAX_VENV"')
    assert p.build["setup"].endswith("&& uv pip install -e . pytest")  # nothing else: Parallax's setup does the rest

    (proj.root / "REVIEW.md").write_text(review.TEMPLATE + "\n7. A rule for this repo only.\n")
    out, _ = run(proj, [case_for(upstream)], scripted(maker(FIXED), FakeChecker()))
    scratch = Project(Path(result(out)["scratch"]))
    [g] = [e for e in scratch.ledger.entries() if e["kind"] == "gate.approved"]
    assert g["actor"] == "parallax" and "within auto_launch_usd $3.00" in g["data"]["rule"]
    assert (scratch.root / "REVIEW.md").read_text() == review.TEMPLATE  # not this repo's own REVIEW.md


# staleness in stats -------------------------------------------------------------------------------------

def test_stats_says_when_the_evals_are_older_than_what_steers_the_agents(proj, upstream, monkeypatch):
    (proj.root / "evals").mkdir()
    (proj.root / evals.CASES_FILE).write_text("")
    assert stats.report(proj)[-1] == "no eval has run on the current pipeline. parallax eval --budget runs one."
    out, _ = run(proj, [case_for(upstream)], scripted(maker(FIXED), FakeChecker()))
    assert stats.report(proj)[-1].startswith(f"evals are current: run {json.loads((out / 'run.json').read_text())['run']}")

    (proj.root / "REVIEW.md").write_text((proj.root / "REVIEW.md").read_text() + "\n- one more rule\n")
    (proj.root / "CLAUDE.md").write_text("rules for agents building this repo\n")
    assert stats.report(proj)[-1].startswith("evals are current")  # no eval agent reads either file

    monkeypatch.setattr(review, "TEMPLATE", review.TEMPLATE + "\n6. One more pass for every repo.\n")
    assert stats.report(proj)[-1].startswith("evals are older than parallax/review.py (TEMPLATE). last run ")
    monkeypatch.setattr(claude, "BLIND_PROMPT", claude.BLIND_PROMPT + " Be brief.")
    (proj.root / POLICY_FILE).write_text((proj.root / POLICY_FILE).read_text().replace(
        'model = "claude-sonnet-5-5"    # the drafters', 'model = "claude-opus-5"    # the drafters'))
    proj.reload_policy()
    assert stats.report(proj)[-1].startswith("evals are older than parallax.policy.toml, parallax/agents/claude.py "
                                             "(BLIND_PROMPT), parallax/review.py (TEMPLATE), Focus's model. ")


def test_a_run_that_finished_no_case_doesnt_make_the_evals_current(proj, upstream):
    (proj.root / "evals").mkdir()
    (proj.root / evals.CASES_FILE).write_text("")
    run(proj, [case_for(upstream)], scripted(maker(FIXED), FakeChecker()), budget=1.0)
    assert stats.report(proj)[-1].startswith("no eval has run")


def test_stats_says_nothing_about_evals_in_a_repo_without_them(proj):
    assert stats.report(proj) == ["no tasks yet."]


# eval check: no model -----------------------------------------------------------------------------------

def test_eval_check_needs_the_hidden_tests_to_fail_at_the_base_and_pass_at_the_fix(proj, upstream):
    command = proj.policy.check["test_command"]
    assert evals.check_case(case_for(upstream), command, plain_runner) == (True, "base 1 of 2 pass, fix 2 of 2 pass")
    repo, base, fix = upstream
    _commit(repo, {"tests/test_other.py": "def test_ok():\n    assert True\n"}, "later")
    later = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    _commit(repo, {"tests/test_new.py": "from calc import sub\n\n\ndef test_sub():\n    assert sub(3, 2) == 1\n"}, "new api")
    newer = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    erroring = evals.Case(**{**case_for(upstream).__dict__, "tests": ["tests/test_new.py"], "base": later, "fix": newer})
    assert evals.check_case(erroring, command, plain_runner) == (
        False, "the hidden tests error at the base before any test runs (exit 2), so they don't show the bug")
    passing = evals.Case(**{**case_for(upstream).__dict__, "tests": ["tests/test_other.py"], "base": later, "fix": later})
    assert evals.check_case(passing, command, plain_runner) == (False, "the hidden tests already pass at the base (1 of 1 pass)")


def test_the_eval_command_needs_a_budget_and_stops_before_spending_past_it(proj, upstream, monkeypatch, capsys):
    (proj.root / "evals").mkdir()
    case = case_for(upstream)
    (proj.root / evals.CASES_FILE).write_text(
        f'[[case]]\nid = "{case.id}"\nrepo = "{case.repo}"\nbase = "{case.base}"\nfix = "{case.fix}"\n'
        f'tests = ["tests/test_calc.py"]\nsetup = []\ngoal = "add subtracts"\n')
    monkeypatch.chdir(proj.root)
    assert main(["eval"]) == 1 and "an eval run needs --budget" in capsys.readouterr().err
    assert main(["eval", "--budget", "2"]) == 0  # the default ceiling is $5: nothing starts, nothing is spawned
    out = capsys.readouterr().out
    assert out.startswith("stopped before calc-1: $0.00 spent, and the next case needs up to $5.50")
    assert "results in evals/results/" in out
    assert main(["eval", "nope", "--budget", "2"]) == 1 and "no case nope" in capsys.readouterr().err
