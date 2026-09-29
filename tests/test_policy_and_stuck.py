import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import ScriptedAgent, good_probe
from parallax import build
from parallax.gate import Scope, make_permission_fn
from test_guard_and_checker import _approved
from parallax.agents.base import Permission
from parallax.agents.claude import rule_on_tool_call
from parallax.cli import main
from parallax.core import ROOT_ENV, TASK_ENV, ParallaxError, Project
from parallax.ledger import Ledger
from parallax.policy import Policy

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


# limits ---------------------------------------------------------------


def test_limits_have_defaults_and_are_validated():
    assert Policy().limits == {"stuck_after": 3, "stale_minutes": 60, "maker_turns": 150}
    assert Policy(limits={"stuck_after": 2}).limits["stuck_after"] == 2
    for bad in ({"stuck_after": 0}, {"stuck_aftr": 2}, {"stuck_after": True}, {"max_parallel": 4}):
        with pytest.raises(ValueError):
            Policy(limits=bad)


def test_the_old_per_action_policy_is_refused_with_a_way_forward():
    for table in ("actions", "exact", "profiles", "made_up"):
        with pytest.raises(ValueError, match="gone|unknown"):
            Policy.from_dict({table: {}})


def test_default_policy_file_loads(repo):
    proj = Project.init(repo)
    assert proj.policy.limits["stuck_after"] == 3


def test_the_example_policy_is_the_default_and_init_copies_it(repo):
    """parallax.policy.example.toml is what init writes: they can't drift apart."""
    from parallax.core import EXAMPLE_POLICY_FILE, POLICY_FILE
    from parallax.policy import DEFAULT_POLICY
    example = (Path(__file__).resolve().parents[1] / EXAMPLE_POLICY_FILE).read_text()
    assert example == DEFAULT_POLICY
    settings = [line for line in example.splitlines() if line and not line.startswith(("#", "["))]
    assert settings and all("  # " in line for line in settings)  # one comment per setting
    (repo / EXAMPLE_POLICY_FILE).write_text(example.replace("rework_cap = 3 ", "rework_cap = 2 "))
    assert Project.init(repo).policy.check["rework_cap"] == 2  # a repo's own example wins
    assert (repo / POLICY_FILE).read_text() == (repo / EXAMPLE_POLICY_FILE).read_text()


# spawn depth 1 ---------------------------------------------------------------

def test_a_task_cant_create_tasks_or_decide(repo, monkeypatch, capsys):
    proj, tid = _approved(repo)
    item = proj.ledger.append("disagreement.raised", "parallax", "a finding", task=tid, stage="check")

    monkeypatch.setenv(TASK_ENV, tid)
    monkeypatch.setenv(ROOT_ENV, str(repo))
    with pytest.raises(ParallaxError, match="tasks can't"):
        proj.new_task("spawned")
    with pytest.raises(ParallaxError, match="tasks can't"):
        proj.resolve(item["id"], True, "approving myself")
    monkeypatch.chdir(repo)
    assert main(["approve", tid]) == 1
    assert "tasks can't" in capsys.readouterr().err
    assert proj.inbox()[0]["id"] == item["id"]

    monkeypatch.setenv(ROOT_ENV, str(repo / "some-other-project"))  # a maker testing parallax itself
    proj.new_task("fine elsewhere")


def test_maker_gets_task_env(repo):
    proj, tid = _approved(repo)
    agent = ScriptedAgent()
    build.run_build(proj, tid, lambda left, settings: agent, preflight_runner=good_probe)
    assert agent.envs[0][TASK_ENV] == tid and agent.envs[0][ROOT_ENV] == str(proj.root)


# stuck -------------------------------------------------------------------------

def test_repeated_refusals_stop_the_agent_and_come_to_you(repo):
    proj, tid = _approved(repo)
    wt = Path(proj.task(tid)["worktree"])
    agent = ScriptedAgent(steps=[("read", "/etc/passwd")] * 5 + [("write", "never.txt", "x")])
    agent.run("go", wt, make_permission_fn(proj, tid, wt, scope=Scope()))
    assert [p.stop for _, _, p in agent.results] == [False, False, True]  # stopped at the third
    [item] = proj.inbox()
    assert item["kind"] == "stuck.raised" and "refused 3 times" in item["reason"]
    assert not (wt / "never.txt").exists()


def test_hook_stops_the_agent_when_the_gate_says_so():
    def stop(action, detail, paths):
        return Permission(False, "stopped: stuck", stop=True)
    out = asyncio.run(rule_on_tool_call(stop, "Bash", {"command": "pytest"}))
    assert out["continue_"] is False and out["stopReason"] == "stopped: stuck"
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    def go(action, detail, paths):
        return Permission(True)
    assert "continue_" not in asyncio.run(rule_on_tool_call(go, "Read", {"file_path": "a"}))
