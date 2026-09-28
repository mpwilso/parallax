"""A project with one inbox item of every kind, for UI tests and the browser check.

Scratch fixtures only: the 'human' rulings in here are scripted test data.
"""
from __future__ import annotations

from pathlib import Path

from fakes import FakeChecker, ScriptedAgent
from parallax.core import POLICY_FILE, Project
from parallax.runner import run_task

POLICY = """\
[actions]
"fs.read"    = "allow"
"fs.write"   = "allow"
"shell.run"  = "ask"
"git.commit" = "ask"
"""


def seed_all_kinds(repo: Path) -> dict[str, str]:
    (repo / POLICY_FILE).write_text(POLICY)
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

    # stuck: the maker keeps trying to write a protected file
    stuck = proj.new_task("tidy the docs")["task"]
    run_task(proj, stuck, ScriptedAgent(steps=[("write", "mission.md", "x")] * 3), FakeChecker())
    ids["stuck"] = proj.inbox()[-1]["id"]

    # an agent waiting on you right now
    waiting = proj.new_task("update the changelog")["task"]
    proj.ledger.append("maker.started", "parallax", "", task=waiting, stage="build")
    ids["permission"] = proj.check(waiting, "shell.run", "git status")["entry"]["id"]

    return ids
