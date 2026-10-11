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


def test_a_line_torn_by_a_crash_is_dropped_before_the_next_append(tmp_path):
    """A writer that died mid-line leaves a tail with no newline. The next append chains to the
    last whole entry, so the torn tail moves to ledger.jsonl.torn, kept as written, instead of
    joining the new line and breaking every read."""
    from parallax.ledger import Ledger
    ledger = Ledger(tmp_path / "ledger.jsonl")
    first = ledger.append("task.created", "human")
    torn = '{"id": "torn", "kind": "task.dra'
    with ledger.path.open("a") as f:
        f.write(torn)
    second = ledger.append("task.approved", "human")
    assert [e["id"] for e in ledger.entries()] == [first["id"], second["id"]]
    assert second["prev"] == first["hash"]
    assert ledger.verify() == (True, "ledger intact")
    assert ledger.path.read_text().endswith("\n")
    side = (tmp_path / "ledger.jsonl.torn").read_bytes().split(b"\n")
    assert side[0].startswith(b"torn tail at 20") and side[0].endswith(b"Z, 32 bytes:")
    assert side[1] == torn.encode() and side[2:] == [b""]  # the bytes exactly, then a newline


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


def counting_loads(monkeypatch):
    """The ledger's json.loads, counted: one call per line parsed."""
    from types import SimpleNamespace
    from parallax import ledger
    calls = []

    def loads(text, *a, **kw):
        calls.append(text)
        return json.loads(text, *a, **kw)
    monkeypatch.setattr(ledger, "json", SimpleNamespace(loads=loads, dumps=json.dumps))
    return calls


def test_reads_of_an_unchanged_ledger_reuse_one_parse(tmp_path, monkeypatch):
    """/api/board reads the ledger several times per task, each request with a new Project. Reads of
    an unchanged file reuse one parse, across Ledger objects; a change to its size or mtime (what
    /api/version reports) is read again, from any writer."""
    import os
    from parallax.ledger import Ledger
    path = tmp_path / "ledger.jsonl"
    for n in range(3):
        Ledger(path).append("task.created", "human", n=n)
    calls = counting_loads(monkeypatch)
    for _ in range(3):
        assert [e["data"]["n"] for e in Ledger(path).entries()] == [0, 1, 2]
    assert len(calls) == 3  # three lines, parsed once

    with path.open("a") as f:  # another process appends
        f.write(json.dumps({"kind": "outside", "data": {"n": 3}}) + "\n")
    assert [e["data"]["n"] for e in Ledger(path).entries()] == [0, 1, 2, 3]

    st = os.stat(path)  # the same size, other bytes, another mtime
    path.write_text(path.read_text().replace('"outside"', '"outsidE"'))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert Ledger(path).entries()[-1]["kind"] == "outsidE"


def test_a_write_through_the_ledger_drops_the_cached_parse(tmp_path):
    """Even a file put back to the cached size and mtime afterwards is read again: a write through
    the Ledger forgets the parse instead of trusting the stat."""
    import os
    from parallax.ledger import Ledger
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    ledger.append("one", "human")
    before, st = path.read_bytes(), os.stat(path)
    assert [e["kind"] for e in ledger.entries()] == ["one"]
    ledger.append("two", "human")
    path.write_bytes(before.replace(b'"one"', b'"uno"'))
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert [e["kind"] for e in ledger.entries()] == ["uno"]


def test_a_torn_tail_is_still_left_out_of_cached_reads(tmp_path):
    from parallax.ledger import Ledger
    ledger = Ledger(tmp_path / "ledger.jsonl")
    first = ledger.append("task.created", "human")
    assert [e["id"] for e in ledger.entries()] == [first["id"]]
    with ledger.path.open("a") as f:
        f.write('{"id": "torn", "kind": "task.dra')
    assert [e["id"] for e in ledger.entries()] == [first["id"]]
    with ledger.path.open("a") as f:  # still being written: nothing new until its newline
        f.write('fted"')
    assert [e["id"] for e in ledger.entries()] == [first["id"]]
    second = ledger.append("task.approved", "human")
    assert [e["id"] for e in ledger.entries()] == [first["id"], second["id"]]
    assert (tmp_path / "ledger.jsonl.torn").read_bytes().split(b"\n")[1] == b'{"id": "torn", "kind": "task.drafted"'
    assert ledger.verify() == (True, "ledger intact")


def test_entries_are_shared_between_reads_so_they_cant_be_changed(tmp_path):
    """A cached entry handed to every reader can't be changed by one of them; a copy can. The list
    itself is each caller's own."""
    import copy
    from parallax.ledger import Ledger
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append("task.created", "human", "a reason", files=["a.py"], meta={"k": 1})
    e = ledger.entries()[0]
    for change in (lambda: e.__setitem__("reason", "x"), lambda: e["data"].update(k=2), lambda: e.pop("id"),
                   lambda: e["data"]["files"].append("b.py"), lambda: e["data"]["meta"].setdefault("j", 0),
                   lambda: e["data"]["files"].sort()):
        with pytest.raises(TypeError, match="read-only"):
            change()
    mine = ledger.entries()
    mine.append({"kind": "mine"})
    assert len(ledger.entries()) == 1 and ledger.entries()[0] == e
    assert json.loads(json.dumps(e, sort_keys=True)) == e and dict(e) == e and {**e}["reason"] == "a reason"
    deep = copy.deepcopy(e)
    deep["data"]["files"].append("b.py")
    assert type(deep) is dict and deep["data"]["files"] == ["a.py", "b.py"] and e["data"]["files"] == ["a.py"]
    shallow = copy.copy(e)
    shallow["reason"] = "x"
    assert e["reason"] == "a reason"
