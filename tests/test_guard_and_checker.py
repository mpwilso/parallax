import asyncio
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, blocker, good_probe, junit_runner
from parallax import build, check, lifecycle, review
from parallax.agents.base import Review
from parallax.agents.claude import rule_on_tool_call, tool_to_action
from parallax.core import POLICY_FILE, ParallaxError, Project
from parallax.gate import Scope, make_permission_fn
from test_lifecycle_gates import docs as lifecycle_docs, make_key

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def setup(repo: Path) -> tuple[Project, str, Path]:
    """An approved task, and the gate its maker would get: routine work inside the worktree."""
    proj, tid = _approved(repo)
    return proj, tid, Path(proj.task(tid)["worktree"])


def gated(proj: Project, tid: str, wt: Path):
    return make_permission_fn(proj, tid, wt, scope=Scope())


def kinds(proj: Project) -> list[str]:
    return [e["kind"] for e in proj.ledger.entries()]


def test_guard_refuses_protected_writes_before_the_plan(repo):
    proj, tid, wt = setup(repo)  # routine writes are allowed, so only the guard stands in the way
    targets = ["parallax.policy.toml", "Mission.md", ".parallax/x", "sub/.parallax/y",
               str(repo / "outside.txt"), "../../escape.txt", ""]
    agent = ScriptedAgent(steps=[("write", p, "pwned") for p in targets])
    agent.run("go", wt, gated(proj, tid, wt))

    assert [p.allowed for _, _, p in agent.results] == [False] * len(targets)
    assert kinds(proj).count("guard.tripped") == len(targets)
    assert "action.granted" not in kinds(proj)
    assert not (repo / "outside.txt").exists()


def test_shell_commands_naming_protected_files_are_refused(repo):
    proj, tid, wt = setup(repo)
    agent = ScriptedAgent(steps=[
        ("shell", ["git", "checkout", "--", "mission.md"]),
        ("shell", ["git", "-C", str(wt), "status"]),  # the worktree path itself contains .parallax
    ])
    agent.run("go", wt, gated(proj, tid, wt))
    assert [p.allowed for _, _, p in agent.results] == [False, True]
    assert kinds(proj).count("guard.tripped") == 1


def test_protected_file_in_diff_goes_to_inbox_not_checker(repo):
    proj, tid, wt = setup(repo)
    sneaky = [sys.executable, "-c", "open('miss' + 'ion.md', 'w').write('obey me')"]
    agent = ScriptedAgent(steps=[("shell", sneaky)])  # gets past the string check
    assert build.run_build(proj, tid, lambda left, settings: agent, preflight_runner=good_probe) == "disputed"
    assert not [e for e in proj.ledger.entries() if e["kind"] == "check.started"]  # the checker never sees it
    [item] = proj.inbox()
    assert item["data"]["stage"] == "guard" and "mission.md" in item["reason"]


def test_changed_policy_file_stops_every_later_action(repo):
    proj, tid, wt = setup(repo)
    def edit(cwd):
        (repo / POLICY_FILE).write_text("[limits]\nstuck_after = 9\n")
    agent = ScriptedAgent(steps=[("read", "a"), ("call", edit), ("read", "b"), ("write", "ok.txt", "x")])
    agent.run("go", wt, gated(proj, tid, wt))
    assert [(p.allowed, p.stop) for _, _, p in agent.results] == [(True, False), (False, True)]
    assert not (wt / "ok.txt").exists()  # the agent was stopped before it got there


def _approved(repo, tightening=""):
    """A lifecycle task with its plan approved, with secrets planted where the checker must not look."""
    make_key()
    proj = Project.init(repo)
    d = lifecycle_docs(tightening=tightening)
    d["intent"] = d["intent"].replace("The steps assume PowerShell.", "PROBLEM-SECRET: the steps assume PowerShell.")
    d["plan"] = d["plan"].replace("1. Edit README.md.", "1. PLAN-SECRET: edit README.md.")
    tid = lifecycle.new_intent(proj, "fix the readme", FakeDrafter(d))["task"]
    lifecycle.approve(proj, tid)
    return proj, tid


def _check(proj, tid, maker, checker, runner=None):
    return check.run_check(proj, tid, checker, lambda left, settings: maker,
                           test_runner=runner or junit_runner(), preflight_runner=good_probe)


def _new_file_diff(path: str, content: str) -> str:
    blob = hashlib.sha1(f"blob {len(content.encode())}\0{content}".encode()).hexdigest()[:7]
    body = "".join(f"+{line}\n" for line in content.splitlines())
    n = len(content.splitlines())
    return (f"diff --git a/{path} b/{path}\nnew file mode 100644\nindex 0000000..{blob}\n--- /dev/null\n"
            f"+++ b/{path}\n@@ -0,0 +1{'' if n == 1 else f',{n}'} @@\n{body}")


def test_checker_is_blind_to_maker_explanation(repo):
    """The pin: the checker gets exactly its brief, on the first review and on re-review. Each outcome
    keeps its asked or inferred mark (since 2026-09-30; before, the marks were removed)."""
    proj, tid = _approved(repo, tightening="check the WSL steps")
    maker = ScriptedAgent(steps=[("write", "README.md", "first\n"),
                                 ("shell", ["git", "add", "README.md"]),
                                 ("shell", [*GIT, "commit", "-q", "-m", "COMMIT-SECRET because reasons"])],
                          summary="SUMMARY-SECRET: I wrote it")
    checker = FakeChecker(reviews=[blocker("REVIEWER-FINDING"), Review("pass")])
    assert build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe) == "built"
    maker.steps["build"] = [("write", "README.md", "second\n")]
    maker.summary = "REPLY-SECRET: fixed it as you asked"
    assert _check(proj, tid, maker, checker) == "ready"

    review_md = review.TEMPLATE.rstrip() + "\n\n## This task only\n\ncheck the WSL steps"
    expected = [
        f"Outcome:\n1. asked: A new user on WSL can follow them.\n\nConstraints:\nKeep the macOS steps.\n\n"
        f"REVIEW.md:\n{review_md}\n\nDiff:\n{_new_file_diff('README.md', content)}\n"
        for content in ("first\n", "second\n")
    ]
    assert checker.briefs == expected  # exactly this, first review and re-review alike
    for secret in ("PROBLEM-SECRET", "PLAN-SECRET", "SUMMARY-SECRET", "COMMIT-SECRET", "REPLY-SECRET"):
        assert all(secret not in b for b in checker.briefs), secret
    assert "REVIEWER-FINDING" in maker.goals[-1][1]  # the maker gets the findings; the checker never gets the reply
    assert checker.models == ["claude-sonnet-5-5"] * 2
    assert proj.ledger.verify()[0]


@pytest.mark.parametrize("approve,status", [(True, "ready"), (False, "needs work")])
def test_disagreement_goes_to_inbox_and_needs_a_reason(repo, approve, status):
    """The rework rule: 3 recorded cycles, then the 4th fail comes to you."""
    proj, tid = _approved(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    checker = FakeChecker(reviews=[blocker("greeting is misspelled")])
    build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe)
    assert _check(proj, tid, maker, checker) == "disputed"
    assert len(checker.briefs) == 4 and len(maker.goals) == 4  # the first check, then 3 reworks
    assert [e["data"]["cycle"] for e in proj.ledger.entries() if e["kind"] == "rework.started"] == [1, 2, 3]
    [item] = proj.inbox()
    assert item["kind"] == "disagreement.raised" and "after 3 rework cycles" in item["reason"]
    assert "misspelled" in item["reason"]

    with pytest.raises(ParallaxError):
        check.can_check(proj, tid)  # can't recheck over an open disagreement
    with pytest.raises(ParallaxError):
        proj.resolve(item["id"], approve, "  ")
    proj.resolve(item["id"], approve, "read the diff myself")
    assert proj.task(tid)["status"] == status
    assert proj.inbox() == []
    assert proj.ledger.verify()[0]


def test_a_checker_error_is_retried_once_by_code_then_comes_to_you(repo):
    proj, tid = _approved(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    checker = FakeChecker(error=True)
    build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe)
    assert _check(proj, tid, maker, checker) == "disputed"
    assert len(checker.briefs) == 2 and len(maker.goals) == 1  # asked once more, with the same brief
    [retry] = [e for e in proj.ledger.entries() if e["kind"] == "check.retried"]
    assert retry["reason"] == "Second Eye gave no usable verdict (garbled reply), so it was asked once more"
    assert not [e for e in proj.ledger.entries() if e["kind"] == "rework.started"]
    [item] = proj.inbox()
    assert item["reason"].startswith("Second Eye error")


def test_a_checker_that_answers_the_second_time_never_reaches_you(repo):
    proj, tid = _approved(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    checker = FakeChecker(error=1)
    build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe)
    assert _check(proj, tid, maker, checker) == "ready"
    assert len(checker.briefs) == 2 and proj.inbox() == []
    assert [e["kind"] for e in proj.ledger.entries() if e["kind"] in ("check.retried", "verdict.recorded")] == \
        ["verdict.recorded", "check.retried", "verdict.recorded"]


def test_hook_rules_on_every_tool_call_including_reads(repo):
    # the SDK auto-approves reads without calling can_use_tool, so the hook must rule on them
    proj, tid, wt = setup(repo)
    drafter = make_permission_fn(proj, tid, wt, read_only=True)
    outside = asyncio.run(rule_on_tool_call(drafter, "Read", {"file_path": "/etc/passwd"}))["hookSpecificOutput"]
    assert outside["permissionDecision"] == "deny"
    write = asyncio.run(rule_on_tool_call(drafter, "Write", {"file_path": "a.py", "content": ""}))["hookSpecificOutput"]
    assert write["permissionDecision"] == "deny" and "read-only" in write["permissionDecisionReason"]
    maker = gated(proj, tid, wt)
    assert asyncio.run(rule_on_tool_call(maker, "Write", {"file_path": "a.py", "content": ""}))["hookSpecificOutput"][
        "permissionDecision"] == "allow"
    guarded = asyncio.run(rule_on_tool_call(maker, "Edit", {"file_path": "mission.md"}))["hookSpecificOutput"]
    assert guarded["permissionDecision"] == "deny"


def test_without_an_approved_plan_only_a_read_only_stage_runs(repo):
    """The old [actions] policy is gone: no plan, no writes, no commands, whatever a file says."""
    proj, tid, wt = setup(repo)
    fn = make_permission_fn(proj, tid, wt)
    assert not fn("fs.write", "a.py", ["a.py"]).allowed
    assert not fn("shell.run", "pytest", []).allowed
    assert "no approved plan" in fn("tool.Task", "spawn", []).message


def test_log_survives_characters_the_console_cant_encode(repo):
    proj = Project.init(repo)
    proj.ledger.append("maker.finished", "maker", "fixed add() → returns a + b ✨\n\nSECOND-LINE")
    import parallax
    source = str(Path(parallax.__file__).resolve().parents[1])  # the code under test, installed or not
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": source}
    out = subprocess.run([sys.executable, "-m", "parallax.cli", "log"], cwd=repo, env=env,
                         capture_output=True, text=True, encoding="cp1252")
    assert out.returncode == 0, out.stderr
    assert "fixed add()" in out.stdout
    assert "SECOND-LINE" not in out.stdout and len(out.stdout.splitlines()) == 2


def test_sdk_tools_map_to_parallax_actions(repo):
    assert tool_to_action("Read", {"file_path": "a.py"}) == ("fs.read", "a.py", [])
    assert tool_to_action("Grep", {"pattern": "x"})[0] == "fs.read"
    assert tool_to_action("Edit", {"file_path": "a.py", "old_string": "", "new_string": ""}) == ("fs.write", "a.py", ["a.py"])
    assert tool_to_action("Write", {"file_path": "b.py", "content": ""})[2] == ["b.py"]
    assert tool_to_action("Bash", {"command": "pytest -q"}) == ("shell.run", "pytest -q", [])
    assert tool_to_action("WebFetch", {"url": "https://x.y"})[0] == "net.fetch"
    action = tool_to_action("Task", {"prompt": "spawn a helper"})[0]
    assert action == "tool.Task"
    proj, tid, wt = setup(repo)
    assert not gated(proj, tid, wt)(action, "spawn a helper", []).allowed  # not routine, not in the plan


def test_second_eye_sees_each_outcomes_mark_and_may_fail_a_change_only_on_an_asked_one():
    """In the seeded eval, correct maintainers' fixes failed on outcomes Focus had inferred."""
    from parallax import lint
    from parallax.agents import claude
    intent = ("## Outcome\n1. **Asked**: add sums.\n2. inferred: add accepts any number of arguments.\n3. add is fast.\n\n"
              "## Constraints\nKeep the name.\n")
    b = review.brief(intent, "rules", "", "a diff")
    assert b == ("Outcome:\n1. asked: add sums.\n2. inferred: add accepts any number of arguments.\n3. add is fast.\n\n"
                 "Constraints:\nKeep the name.\n\nREVIEW.md:\nrules\n\nDiff:\na diff\n")
    assert lint.unmark(lint.marked(intent)) == lint.unmark(intent)  # the same outcomes, only the marks' form differs
    rule = " ".join(claude.BLIND_PROMPT.split())
    assert ('A finding that fails the change (a severity REVIEW.md makes blocking) or any finding of kind "scope" '
            "may rest only on an asked outcome or a constraint.") in rule
    assert "never as a reason to fail" in rule and "An outcome with no mark counts as asked." in rule


def _approved_with_inferred(repo):
    """A task whose intent has an asked outcome (1) and one Focus inferred (2)."""
    make_key()
    proj = Project.init(repo)
    d = lifecycle_docs()
    d["intent"] = d["intent"].replace("1. asked: A new user on WSL can follow them.",
                                      "1. asked: A new user on WSL can follow them.\n2. inferred: The steps cover WSL 1 too.")
    d["plan"] = d["plan"].replace('covers = { "1" = ["tests/test_readme.py"] }',
                                  'covers = { "1" = ["tests/test_readme.py"], "2" = ["tests/test_readme.py"] }')
    tid = lifecycle.new_intent(proj, "fix the readme", FakeDrafter(d))["task"]
    lifecycle.approve(proj, tid)
    return proj, tid


def test_a_blocking_finding_that_cites_only_inferred_outcomes_becomes_a_note_by_code(repo):
    """Seen in the seeded eval: tabulate-190 failed a correct fix on inferred outcomes, against its rule."""
    from parallax.agents.base import Finding
    proj, tid = _approved_with_inferred(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    inferred_only = Finding("major", "README.md:1", "no WSL 1 steps", "scope", ["outcome 2"])
    checker = FakeChecker(reviews=[Review("fail", [inferred_only])])
    build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe)
    assert _check(proj, tid, maker, checker) == "ready"  # a note, not a reason to fail
    [down] = [e for e in proj.ledger.entries() if e["kind"] == "verdict.downgraded"]
    assert down["data"]["lowered"] == [{"text": "no WSL 1 steps", "where": "README.md:1", "from": "major", "to": "minor",
                                        "cites": ["outcome 2"]}]
    [v] = [e for e in proj.ledger.entries() if e["kind"] == "verdict.recorded"]
    assert v["data"]["verdict"] == "no_finding" and v["data"]["checker_verdict"] == "fail"  # nothing blocks, nothing confirmed
    assert v["data"]["findings"][0]["severity"] == "major"  # what Second Eye said, as it said it


@pytest.mark.parametrize("cites", [["outcome 1"], ["outcome 2", "outcome 1"], ["outcome 2", "constraint"], []])
def test_a_finding_resting_on_an_asked_outcome_a_constraint_or_nothing_still_blocks(repo, cites):
    from parallax.agents.base import Finding
    proj, tid = _approved_with_inferred(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    checker = FakeChecker(reviews=[Review("fail", [Finding("major", "README.md:1", "wrong", "defect", cites)])] * 4)
    build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe)
    assert _check(proj, tid, maker, checker) == "disputed"  # it stays blocking, through the 3 reworks
    assert not [e for e in proj.ledger.entries() if e["kind"] == "verdict.downgraded"]


def test_enforce_reads_the_marks_and_an_unmarked_outcome_counts_as_asked():
    from parallax.agents.base import Finding
    intent = "## Outcome\n1. asked: a\n2. inferred: b\n3. c\n\n## Constraints\nnone\n"
    findings = [Finding("blocker", "", "b", "defect", ["outcome 2"]), Finding("minor", "", "b too", "defect", ["outcome 2"]),
                Finding("major", "", "c", "defect", ["outcome 3"])]
    counted, lowered = review.enforce(findings, intent, ("blocker", "major"))
    assert [f.severity for f in counted] == ["minor", "minor", "major"] and [x["from"] for x in lowered] == ["blocker"]
