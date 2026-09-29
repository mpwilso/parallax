import json
from pathlib import Path

import pytest

from parallax.core import Project
from parallax.policy import Policy


def test_merge_cannot_be_delegated():
    """Merging isn't a setting anywhere: no policy table can name it, and there's no merge command."""
    for data in ({"actions": {"git.merge": "allow"}}, {"merge": {"auto": True}}):
        with pytest.raises(ValueError):
            Policy.from_dict(data)
    from parallax.cli import main
    with pytest.raises(SystemExit):
        main(["merge", "abc123"])


def test_task_gets_its_own_worktree(repo):
    proj = Project.init(repo)
    t = proj.new_task("fix the thing")
    wt = Path(t["worktree"])
    assert wt.exists() and wt != repo
    (wt / "new.txt").write_text("hello\n")
    assert "new.txt" in proj.diff(t["task"])
    assert not (repo / "new.txt").exists()


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
