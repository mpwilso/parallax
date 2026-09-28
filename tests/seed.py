"""A project with one inbox item of every kind, for UI tests and the browser check.

Scratch fixtures only: the 'human' rulings in here are scripted test data.
"""
from __future__ import annotations

from pathlib import Path

from fakes import FakeChecker, FakeConductor, ScriptedAgent
from parallax.agents.base import Law, Proposal, Recommendation
from parallax.core import POLICY_FILE, Project
from parallax.evidence import raise_promotions
from parallax.inbox import split_goal
from parallax.mission import MISSION_FILE
from parallax.pulse import pulse
from parallax.runner import run_task

POLICY = """\
[actions]
"fs.read"    = "allow"
"fs.write"   = "allow"
"shell.run"  = "ask"
"git.commit" = "ask"
"""
MISSION = "# Mission\n\n## who\nThe conductor for calc.\n\n## how\nKeep diffs small.\n"


def seed_all_kinds(repo: Path) -> dict[str, str]:
    (repo / POLICY_FILE).write_text(POLICY)
    (repo / MISSION_FILE).write_text(MISSION)
    proj = Project.init(repo)
    ids: dict[str, str] = {}

    # a disagreement: the checker flags the maker's change to calc.py
    fix = proj.new_task("fix add() so it returns the sum")["task"]
    run_task(proj, fix, ScriptedAgent(steps=[("write", "calc.py", "def add(a, b):\n    return a + b if a > 0 else b\n")],
                                      summary="Fixed add(); it now returns the sum.", cost=0.42),
             FakeChecker(verdict="fail", findings=["add() now ignores negative numbers"]))
    ids["disagreement"] = proj.inbox()[-1]["id"]

    # a ready task, to show the merge hint
    ready = proj.new_task("add a mul() function")["task"]
    run_task(proj, ready, ScriptedAgent(steps=[("write", "mul.py", "def mul(a, b):\n    return a * b\n")], cost=0.31),
             FakeChecker(verdict="pass"))
    ids["ready_task"] = ready

    # stuck: the maker keeps trying to edit the mission
    stuck = proj.new_task("tidy the docs")["task"]
    run_task(proj, stuck, ScriptedAgent(steps=[("write", "mission.md", "x")] * 3), FakeChecker())
    ids["stuck"] = proj.inbox()[-1]["id"]

    # evidence: 10 approvals of the test command, 3 rejections of commits
    helpers = [proj.new_task(f"helper {i}")["task"] for i in range(3)]
    for i in range(10):
        t = proj.task(helpers[i % 3])
        req = proj.check(t["task"], "shell.run", "python -m pytest -q")["entry"]
        proj.resolve(req["id"], True, "running the tests is fine")
    rejections = []
    for i, why in enumerate(["I review before anything is committed", "don't commit, leave it staged for me",
                             "no commits from agents, I do that"]):
        req = proj.check(helpers[i], "git.commit", "commit -m wip")["entry"]
        rejections.append(proj.resolve(req["id"], False, why)["id"])
    ids["promotion"] = raise_promotions(proj)[0]["id"]

    # an agent waiting on you right now
    waiting = proj.new_task("update the changelog")["task"]
    proj.ledger.append("maker.started", "parallax", "", task=waiting, stage="build")
    ids["permission"] = proj.check(waiting, "shell.run", "git status")["entry"]["id"]

    # a law and a recommendation from the conductor, and proposals from a goal
    rule = {"profile": "default", "action": "git.commit", "key": None, "ruling": "deny"}
    conductor = FakeConductor(
        laws=[Law("Agents never commit. The human reviews and commits.", rejections, rule)],
        recommend=lambda snap: [Recommendation(ids["permission"], "approve", "read-only command, harmless")],
        split=[Proposal("add a divide() function that refuses to divide by zero", "asked for in the goal", "default", True)],
    )
    pulse(proj, conductor, launch=lambda project, tid: 0)
    ids["law"] = [e for e in proj.inbox() if e["kind"] == "law.raised"][0]["id"]
    ids["proposal"] = split_goal(proj, conductor, "grow the calculator")[0]["id"]
    return ids
