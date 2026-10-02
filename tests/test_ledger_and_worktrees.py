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


def ids(monkeypatch, *prefixes):
    """uuid4, drawing task ids in this order: a clash on purpose, not by a one-in-a-million chance."""
    import uuid
    from types import SimpleNamespace
    from parallax import core
    drawn = iter(prefixes)  # core's own uuid only: the ledger's entry ids stay random
    monkeypatch.setattr(core, "uuid", SimpleNamespace(uuid4=lambda: uuid.UUID(hex=next(drawn) + "0" * 26)))


def test_a_task_id_whose_branch_exists_draws_another(repo, monkeypatch):
    """CI run 36947648494: two tasks drew one id, and the second failed on its branch."""
    proj = Project.init(repo)
    ids(monkeypatch, "d93f08", "d93f08", "5be1aa")
    first = proj.new_task("an earlier task")["task"]
    second = proj.new_task("an earlier task")["task"]
    assert (first, second) == ("d93f08", "5be1aa")
    assert proj.task(second)["branch"] == "parallax/5be1aa-earlier-task" and Path(proj.task(second)["worktree"]).exists()


def test_an_id_from_a_finished_task_is_never_reused(repo, monkeypatch):
    """After a merge the branch and worktree are gone, but the id stays in the ledger: a new task with
    it would merge into the old one's history there."""
    import subprocess
    proj = Project.init(repo)
    ids(monkeypatch, "d93f08", "d93f08", "5be1aa")
    old = proj.new_task("an earlier task")
    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", old["worktree"]], check=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-D", old["branch"]], check=True, capture_output=True)
    assert proj.new_task("a later task")["task"] == "5be1aa"
    assert proj.task("d93f08")["goal"] == "an earlier task"


def test_a_git_failure_says_gits_error_and_the_command_not_its_progress_line(repo):
    """git writes "Preparing worktree (new branch ...)" to stderr before its error, and that first line
    was all the message said."""
    import subprocess
    from parallax.core import ParallaxError, _git, git_failed
    subprocess.run(["git", "-C", str(repo), "branch", "parallax/d93f08-earlier-task"], check=True)
    with pytest.raises(ParallaxError) as err:
        _git(repo, "worktree", "add", "-b", "parallax/d93f08-earlier-task", str(repo.parent / "wt"), "HEAD")
    said = str(err.value)
    assert said.startswith("git worktree add -b parallax/d93f08-earlier-task ")
    assert "failed: fatal: a branch named 'parallax/d93f08-earlier-task' already exists" in said
    assert "Preparing worktree" not in said
    stderr = ("Preparing worktree (checking out 'x')\nerror: Your local changes would be overwritten:\n\tREADME.md\n"
              "hint: commit or stash them\n")
    assert str(git_failed(["checkout", "x"], stderr)) == \
        "git checkout x failed: error: Your local changes would be overwritten: README.md"
    assert str(git_failed(["status"], b"something odd\n")) == "git status failed: something odd"  # no error line: all of it
