import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, ScriptedAgent
from parallax.agents.base import Permission
from parallax.agents.claude import rule_on_tool_call
from parallax.cli import main
from parallax.core import POLICY_FILE, ROOT_ENV, TASK_ENV, ParallaxError, Project
from parallax.ledger import Ledger
from parallax.policy import Policy
from parallax.runner import run_task

POLICY = """\
[actions]
"fs.read"   = "allow"
"fs.write"  = "allow"
"shell.run" = "deny"
{extra}
"""


def setup(repo: Path, extra: str = "") -> Project:
    (repo / POLICY_FILE).write_text(POLICY.format(extra=extra))
    return Project.init(repo)


def kinds(proj: Project) -> list[str]:
    return [e["kind"] for e in proj.ledger.entries()]


# ledger --------------------------------------------------------------------

def test_ledger_chain_survives_parallel_processes(tmp_path):
    path = tmp_path / "ledger.jsonl"
    code = ("import sys; from parallax.ledger import Ledger; l = Ledger(sys.argv[1]); "
            "[l.append('x', 'p' + sys.argv[2], str(i)) for i in range(25)]")
    procs = [subprocess.Popen([sys.executable, "-c", code, str(path), str(n)]) for n in range(4)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    ledger = Ledger(path)
    assert len(ledger.entries()) == 100
    assert ledger.verify() == (True, "ledger intact")


# profiles and limits ---------------------------------------------------------

def test_profiles_are_full_tables_and_readonly_is_fixed():
    p = Policy({"fs.read": "allow", "fs.write": "allow"}, {"docs": {"fs.read": "allow"}})
    assert p.ruling("fs.write") == "allow"
    assert p.ruling("fs.write", "docs") == "deny"  # not inherited from [actions]
    assert p.ruling("fs.write", "readonly") == "deny"
    assert p.ruling("fs.read", "readonly") == "allow"
    with pytest.raises(ValueError):
        Policy({}, {"readonly": {"fs.write": "allow"}})
    with pytest.raises(ValueError):
        Policy({}, {"docs": {"git.merge": "allow"}})
    with pytest.raises(ValueError):
        p.ruling("fs.read", "nope")


def test_limits_have_defaults_and_are_validated():
    assert Policy({}).limits == {"max_parallel": 4, "stuck_after": 3, "stale_minutes": 60,
                                 "promote_after": 10, "evidence_days": 30, "law_after": 3}
    assert Policy({}, limits={"max_parallel": 2}).limits["max_parallel"] == 2
    for bad in ({"max_parallel": 0}, {"max_paralel": 2}, {"stuck_after": True}):
        with pytest.raises(ValueError):
            Policy({}, limits=bad)


def test_default_policy_file_loads(repo):
    proj = Project.init(repo)
    assert proj.policy.limits["max_parallel"] == 4
    assert set(proj.policy.profiles) == {"default", "readonly"}


def test_readonly_task_is_an_investigator(repo):
    proj = setup(repo)  # the default profile allows writes; readonly must not
    t = proj.new_task("why is add() wrong?", profile="readonly")
    agent = ScriptedAgent(steps=[("read", "calc.py"), ("write", "a.txt", "x")], summary="add subtracts")
    checker = FakeChecker()
    assert run_task(proj, t["task"], agent, checker) == "reported"
    assert [p.allowed for _, _, p in agent.results] == [True, False]
    assert checker.calls == []
    [report] = [e for e in proj.ledger.entries() if e["kind"] == "report.recorded"]
    assert report["data"]["text"] == "add subtracts"
    with pytest.raises(ParallaxError):
        proj.new_task("x", profile="nope")


# spawn depth 1 ---------------------------------------------------------------

def test_a_task_cant_create_tasks_or_decide(repo, monkeypatch, capsys):
    proj = setup(repo, '"git.commit" = "ask"')
    tid = proj.new_task("x")["task"]
    pending = proj.check(tid, "git.commit")["entry"]

    monkeypatch.setenv(TASK_ENV, tid)
    monkeypatch.setenv(ROOT_ENV, str(repo))
    with pytest.raises(ParallaxError, match="tasks can't"):
        proj.new_task("spawned")
    with pytest.raises(ParallaxError, match="tasks can't"):
        proj.resolve(pending["id"], True, "approving myself")
    monkeypatch.chdir(repo)
    assert main(["approve", pending["id"], "--reason", "approving myself"]) == 1
    assert "tasks can't" in capsys.readouterr().err
    assert proj.inbox()[0]["id"] == pending["id"]

    monkeypatch.setenv(ROOT_ENV, str(repo / "some-other-project"))  # a maker testing parallax itself
    proj.new_task("fine elsewhere")


def test_maker_gets_task_env(repo):
    proj = setup(repo)
    tid = proj.new_task("x")["task"]
    agent = ScriptedAgent()
    run_task(proj, tid, agent, FakeChecker())
    assert agent.envs == [{TASK_ENV: tid, ROOT_ENV: str(proj.root)}]


# stuck -------------------------------------------------------------------------

@pytest.mark.parametrize("approve,after", [(True, "open"), (False, "closed")])
def test_repeated_refusals_stop_the_task(repo, approve, after):
    proj = setup(repo)
    tid = proj.new_task("run the tests")["task"]
    agent = ScriptedAgent(steps=[("shell", ["pytest"])] * 5 + [("write", "never.txt", "x")])
    checker = FakeChecker()
    assert run_task(proj, tid, agent, checker) == "stuck"
    assert [p.stop for _, _, p in agent.results] == [False, False, True]  # stopped at the third
    assert checker.calls == []
    [item] = proj.inbox()
    assert item["kind"] == "stuck.raised" and "refused 3 times" in item["reason"]
    with pytest.raises(ParallaxError):
        run_task(proj, tid, agent, checker)  # open inbox item
    proj.resolve(item["id"], approve, "looked at it")
    assert proj.task(tid)["status"] == after


def test_hook_stops_the_agent_when_the_gate_says_so():
    stop = lambda action, detail, paths: Permission(False, "stopped: stuck", stop=True)
    out = asyncio.run(rule_on_tool_call(stop, "Bash", {"command": "pytest"}))
    assert out["continue_"] is False and out["stopReason"] == "stopped: stuck"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    go = lambda action, detail, paths: Permission(True)
    assert "continue_" not in asyncio.run(rule_on_tool_call(go, "Read", {"file_path": "a"}))


# caps and queue ------------------------------------------------------------------

def test_run_respects_max_parallel(repo):
    proj = setup(repo, "[limits]\nmax_parallel = 1")
    busy, waiting = proj.new_task("a")["task"], proj.new_task("b")["task"]
    proj.ledger.append("maker.started", "parallax", "", task=busy, stage="build")
    with pytest.raises(ParallaxError, match="max_parallel"):
        run_task(proj, waiting, ScriptedAgent(), FakeChecker())


def test_queue_and_stored_plan_flag(repo):
    proj = setup(repo)
    t = proj.new_task("x", plan=True, queue=True)
    assert t["status"] == "queued" and t["plan"] is True
    with pytest.raises(ParallaxError):
        proj.queue_task(t["task"])  # already queued
    agent = ScriptedAgent()
    run_task(proj, t["task"], agent, FakeChecker())
    assert [stage for stage, _ in agent.goals] == ["plan", "build"]  # plan came from the task
