import json
from pathlib import Path

import pytest

from parallax.core import ParallaxError, Project
from parallax.policy import Policy


def test_unlisted_actions_are_denied():
    assert Policy({}).ruling("anything.at.all") == "deny"


def test_merge_cannot_be_delegated():
    with pytest.raises(ValueError):
        Policy({"git.merge": "allow"})


def test_task_gets_its_own_worktree(repo):
    proj = Project.init(repo)
    t = proj.new_task("fix the thing")
    wt = Path(t["worktree"])
    assert wt.exists() and wt != repo
    (wt / "new.txt").write_text("hello\n")
    assert "new.txt" in proj.diff(t["task"])
    assert not (repo / "new.txt").exists()


def test_rulings_are_logged_and_ask_goes_to_inbox(repo):
    proj = Project.init(repo)
    tid = proj.new_task("x")["task"]
    assert proj.check(tid, "fs.read")["ruling"] == "allow"
    assert proj.check(tid, "git.push")["ruling"] == "deny"
    assert proj.check(tid, "made.up")["ruling"] == "deny"
    pending = proj.check(tid, "shell.run", "run tests")["entry"]
    assert [e["id"] for e in proj.inbox()] == [pending["id"]]

    with pytest.raises(ParallaxError):
        proj.resolve(pending["id"], True, "   ")
    proj.resolve(pending["id"], True, "tests only, fine")
    assert proj.inbox() == []

    kinds = [e["kind"] for e in proj.ledger.entries()]
    assert kinds.count("action.refused") == 2
    assert "decision.resolved" in kinds


def test_ledger_detects_tampering(repo):
    proj = Project.init(repo)
    proj.new_task("x")
    assert proj.ledger.verify()[0]
    lines = proj.ledger.path.read_text().splitlines()
    first = json.loads(lines[0])
    first["reason"] = "rewritten history"
    lines[0] = json.dumps(first, sort_keys=True)
    proj.ledger.path.write_text("\n".join(lines) + "\n")
    ok, msg = proj.ledger.verify()
    assert not ok and "line 1" in msg
