import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, ScriptedAgent
from parallax.agents.claude import rule_on_tool_call, tool_to_action
from parallax.core import POLICY_FILE, ParallaxError, Project
from parallax.gate import make_permission_fn
from parallax.runner import run_task

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


def test_checker_is_blind_to_maker_explanation(repo):
    proj, tid, wt = setup(repo)
    agent = ScriptedAgent(
        plan="PLAN-SECRET: write hello.txt",
        steps=[
            ("write", "hello.txt", "hello\n"),
            ("shell", ["git", "add", "hello.txt"]),
            ("shell", [*GIT, "commit", "-q", "-m", "COMMIT-SECRET because reasons"]),
        ],
        summary="SUMMARY-SECRET: I wrote hello.txt",
    )
    checker = FakeChecker()
    assert run_task(proj, tid, agent, checker, plan=True) == "ready"

    (_, plan_material, k1), (goal, diff, k2) = checker.calls
    assert (k1, k2) == ("plan", "diff")
    assert "PLAN-SECRET" in plan_material
    assert goal == "add a greeting file" and "hello.txt" in diff and "+hello" in diff
    for secret in ("PLAN-SECRET", "COMMIT-SECRET", "SUMMARY-SECRET"):
        assert secret not in goal + diff
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
    proj, tid, wt = setup(repo)
    checker = FakeChecker(verdict="fail", findings=["greeting is misspelled"])
    assert run_task(proj, tid, ScriptedAgent(), checker) == "disputed"
    [item] = proj.inbox()
    assert item["kind"] == "disagreement.raised" and "misspelled" in item["reason"]

    with pytest.raises(ParallaxError):
        run_task(proj, tid, ScriptedAgent(), checker)  # can't rerun over an open disagreement
    with pytest.raises(ParallaxError):
        proj.resolve(item["id"], approve, "  ")
    proj.resolve(item["id"], approve, "read the diff myself")
    assert proj.task(tid)["status"] == status
    assert proj.inbox() == []
    assert proj.ledger.verify()[0]


def test_checker_error_goes_to_inbox_without_retry(repo):
    proj, tid, wt = setup(repo)
    checker = FakeChecker(error=True)
    assert run_task(proj, tid, ScriptedAgent(), checker) == "disputed"
    assert len(checker.calls) == 1
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
