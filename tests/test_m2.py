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
from parallax.gate import make_permission_fn
from parallax.runner import run_task
from test_m8 import docs as lifecycle_docs, make_key

POLICY = """\
[actions]
"fs.read" = "allow"
"fs.write" = "{write}"
"shell.run" = "{shell}"
"""
GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=t"]


def setup(repo: Path, write="allow", shell="allow") -> tuple[Project, str, Path]:
    (repo / POLICY_FILE).write_text(POLICY.format(write=write, shell=shell))
    proj = Project.init(repo)
    t = proj.new_task("add a greeting file")
    return proj, t["task"], Path(t["worktree"])


def kinds(proj: Project) -> list[str]:
    return [e["kind"] for e in proj.ledger.entries()]


def test_guard_refuses_protected_writes_before_policy(repo):
    proj, tid, wt = setup(repo)  # fs.write is "allow", so only the guard stands in the way
    targets = ["parallax.policy.toml", "Mission.md", ".parallax/x", "sub/.parallax/y",
               str(repo / "outside.txt"), "../../escape.txt", ""]
    agent = ScriptedAgent(steps=[("write", p, "pwned") for p in targets])
    run_task(proj, tid, agent, FakeChecker())

    assert [p.allowed for _, _, p in agent.results] == [False] * len(targets)
    assert kinds(proj).count("guard.tripped") == len(targets)
    assert "action.granted" not in kinds(proj) and "decision.requested" not in kinds(proj)
    assert not (repo / "outside.txt").exists()


def test_shell_commands_naming_protected_files_are_refused(repo):
    proj, tid, wt = setup(repo)
    agent = ScriptedAgent(steps=[
        ("shell", ["git", "checkout", "--", "mission.md"]),
        ("shell", ["git", "-C", str(wt), "status"]),  # the worktree path itself contains .parallax
    ])
    run_task(proj, tid, agent, FakeChecker())
    assert [p.allowed for _, _, p in agent.results] == [False, True]
    assert kinds(proj).count("guard.tripped") == 1


def test_protected_file_in_diff_goes_to_inbox_not_checker(repo):
    proj, tid, wt = setup(repo)
    sneaky = [sys.executable, "-c", "open('miss' + 'ion.md', 'w').write('obey me')"]
    agent = ScriptedAgent(steps=[("shell", sneaky)])  # gets past the string check
    checker = FakeChecker()
    assert run_task(proj, tid, agent, checker) == "disputed"
    assert checker.calls == []
    [item] = proj.inbox()
    assert item["data"]["stage"] == "guard" and "mission.md" in item["reason"]


def test_changed_policy_file_stops_every_later_action(repo):
    proj, tid, wt = setup(repo)
    edit = lambda cwd: (repo / POLICY_FILE).write_text(POLICY.format(write="allow", shell="allow") + "# edited\n")
    agent = ScriptedAgent(steps=[("read", "a"), ("call", edit), ("read", "b"), ("write", "ok.txt", "x")])
    checker = FakeChecker()
    assert run_task(proj, tid, agent, checker) == "disputed"
    assert [(p.allowed, p.stop) for _, _, p in agent.results] == [(True, False), (False, True)]
    assert not (wt / "ok.txt").exists()  # the agent was stopped before it got there
    assert checker.calls == []


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
    """The pin: the checker gets exactly its brief, on the first review and on re-review."""
    proj, tid = _approved(repo, tightening="check the WSL steps")
    maker = ScriptedAgent(steps=[("write", "README.md", "first\n"),
                                 ("shell", ["git", "add", "README.md"]),
                                 ("shell", [*GIT, "commit", "-q", "-m", "COMMIT-SECRET because reasons"])],
                          summary="SUMMARY-SECRET: I wrote it")
    checker = FakeChecker(reviews=[blocker("REVIEWER-FINDING"), Review("pass")])
    assert build.run_build(proj, tid, lambda left, settings: maker) == "built"
    maker.steps["build"] = [("write", "README.md", "second\n")]
    maker.summary = "REPLY-SECRET: fixed it as you asked"
    assert _check(proj, tid, maker, checker) == "ready"

    review_md = review.TEMPLATE.rstrip() + "\n\n## This task only\n\ncheck the WSL steps"
    expected = [
        f"Outcome:\n1. A new user on WSL can follow them.\n\nConstraints:\nKeep the macOS steps.\n\n"
        f"REVIEW.md:\n{review_md}\n\nDiff:\n{_new_file_diff('README.md', content)}\n"
        for content in ("first\n", "second\n")
    ]
    assert checker.briefs == expected  # exactly this, first review and re-review alike
    for secret in ("PROBLEM-SECRET", "PLAN-SECRET", "SUMMARY-SECRET", "COMMIT-SECRET", "REPLY-SECRET"):
        assert all(secret not in b for b in checker.briefs), secret
    assert "REVIEWER-FINDING" in maker.goals[-1][1]  # the maker gets the findings; the checker never gets the reply
    assert checker.models == ["claude-sonnet-5-5"] * 2
    assert proj.ledger.verify()[0]


@pytest.mark.parametrize("verdict", ["pass", "no_finding"])
def test_agreement_makes_task_ready(repo, verdict):
    proj, tid, wt = setup(repo)
    assert run_task(proj, tid, ScriptedAgent(), FakeChecker(verdict=verdict)) == "ready"
    assert proj.inbox() == []
    [v] = [e for e in proj.ledger.entries() if e["kind"] == "verdict.recorded"]
    assert v["data"]["verdict"] == verdict


@pytest.mark.parametrize("approve,status", [(True, "ready"), (False, "needs work")])
def test_disagreement_goes_to_inbox_and_needs_a_reason(repo, approve, status):
    """The rework rule: 3 recorded cycles, then the 4th fail comes to you."""
    proj, tid = _approved(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    checker = FakeChecker(reviews=[blocker("greeting is misspelled")])
    build.run_build(proj, tid, lambda left, settings: maker)
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


def test_checker_error_goes_to_inbox_without_retry(repo):
    proj, tid = _approved(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", "x\n")])
    checker = FakeChecker(error=True)
    build.run_build(proj, tid, lambda left, settings: maker)
    assert _check(proj, tid, maker, checker) == "disputed"
    assert len(checker.briefs) == 1 and len(maker.goals) == 1
    assert not [e for e in proj.ledger.entries() if e["kind"] == "rework.started"]
    [item] = proj.inbox()
    assert item["reason"].startswith("checker error")


def test_maker_giving_up_skips_the_checker(repo):
    proj, tid, wt = setup(repo)
    checker = FakeChecker()
    assert run_task(proj, tid, ScriptedAgent(status="gave_up"), checker) == "maker failed"
    assert checker.calls == []


def test_hook_rules_on_every_tool_call_including_reads(repo):
    # the SDK auto-approves reads without calling can_use_tool, so the hook must rule on them
    (repo / POLICY_FILE).write_text('[actions]\n"fs.write" = "allow"\n')  # fs.read unlisted: denied
    proj = Project.init(repo)
    t = proj.new_task("x")
    fn = make_permission_fn(proj, t["task"], Path(t["worktree"]))

    read = asyncio.run(rule_on_tool_call(fn, "Read", {"file_path": "calc.py"}))["hookSpecificOutput"]
    assert read["permissionDecision"] == "deny" and "denied by default" in read["permissionDecisionReason"]
    write = asyncio.run(rule_on_tool_call(fn, "Write", {"file_path": "a.py", "content": ""}))["hookSpecificOutput"]
    assert write["permissionDecision"] == "allow"
    guarded = asyncio.run(rule_on_tool_call(fn, "Edit", {"file_path": "mission.md"}))["hookSpecificOutput"]
    assert guarded["permissionDecision"] == "deny"
    assert kinds(proj)[-3:] == ["action.refused", "action.granted", "guard.tripped"]


def test_log_survives_characters_the_console_cant_encode(repo):
    proj = Project.init(repo)
    proj.ledger.append("maker.finished", "maker", "fixed add() → returns a + b ✨\n\nSECOND-LINE")
    env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
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
    assert proj.check(tid, action)["ruling"] == "deny"
