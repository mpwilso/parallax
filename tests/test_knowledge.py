"""The knowledge layer: what past tasks settled reaches Focus and Maker as data, and nobody else."""
import shutil

import pytest
from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import build, knowledge, lifecycle, pilot
from parallax.accept import accept
from parallax.core import Project
from test_lifecycle_gates import INTENT, WANT, docs, make_key


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def run_pilot(proj, tid, drafter, maker=None, checker=None):
    maker = maker or ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    checker = checker or FakeChecker()
    status = build.run_mode(proj, tid, "pilot", drafter, lambda left, settings: maker, checker,
                            test_runner=junit_runner(), preflight_runner=good_probe)
    return status, maker, checker


def test_a_repo_with_no_finished_tasks_tells_the_agents_nothing(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    drafter = FakeDrafter(docs())
    status, maker, _ = run_pilot(proj, tid, drafter)  # an unfinished task of its own is not history
    assert status == "ready"
    assert knowledge.material(proj) == ""
    assert all(knowledge.HEADING not in r for r in drafter.requests)
    assert all(knowledge.HEADING not in g for _, g in maker.goals)


def test_focus_and_maker_hear_what_past_tasks_settled_and_second_eye_never_does(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    first = pilot.intake(proj, WANT)["task"]
    assert run_pilot(proj, first, FakeDrafter(docs()))[0] == "ready"
    pilot.redraft(proj, first, "keep the macOS steps word for word")
    assert run_pilot(proj, first, FakeDrafter(docs()))[0] == "ready"
    proj.ledger.append("risk.accepted", "human", "the .env in tests is a fixture", task=first, hits=[".env"])
    accept(proj, first)
    dropped = pilot.intake(proj, "rename the project")["task"]
    proj.ledger.append("task.rejected", "human", "not now: the name stays", task=dropped)

    second = pilot.intake(proj, "the install steps still mention PowerShell")["task"]
    drafter = FakeDrafter(docs())
    status, maker, checker = run_pilot(proj, second, drafter)
    assert status == "ready"

    known = knowledge.material(proj, exclude=second)
    assert known.startswith(knowledge.HEADING)
    lines = known.splitlines()
    assert lines[2].startswith(f"- {dropped}, dropped ") and lines[2].endswith(": rename the project")
    assert lines[3] == "  you dropped it: not now: the name stays"
    assert lines[4].startswith(f"- {first}, accepted ") and lines[4].endswith(": fixing the README install steps (docs)")
    assert lines[5:] == ["  outcomes: A new user on WSL can follow them.",
                         "  you sent it back: keep the macOS steps word for word",
                         "  you accepted a risk: the .env in tests is a fixture"]
    assert all(known in r for r in drafter.requests)  # the intent and the plan were drafted with it
    assert all(known in g for _, g in maker.goals)
    assert all(knowledge.HEADING not in b and "macOS steps word for word" not in b for b in checker.briefs)
    assert proj.ledger.verify()[0]


def test_the_latest_ten_finished_tasks_are_given_newest_first(proj):
    ids = []
    for n in range(12):
        tid = proj.new_task(f"task number {n}", intent=True)["task"]
        lifecycle._write(lifecycle.doc_path(proj, tid, "intent"),
                         INTENT.format(size="small").replace("fixing the README install steps", f"doing thing {n}"))
        proj.ledger.append("task.accepted", "human", "", task=tid, commit="0" * 40)
        ids.append(tid)
    blocks = knowledge.summary(proj)
    assert len(blocks) == knowledge.TASKS
    assert [b.split(", ")[0][2:] for b in blocks] == list(reversed(ids))[:10]
    assert blocks[0].splitlines()[0].endswith(": doing thing 11 (docs)")
    assert knowledge.summary(proj, exclude=ids[-1])[0].splitlines()[0].endswith(": doing thing 10 (docs)")


def test_a_reason_is_one_line_and_capped(proj):
    tid = proj.new_task("a task", intent=True)["task"]
    long = "first line\nsecond line " + "x" * 300
    proj.ledger.append("task.rejected", "human", long, task=tid)
    [block] = knowledge.summary(proj)
    reason = block.splitlines()[1]
    assert reason.startswith("  you dropped it: first line second line xxx") and reason.endswith("...")
    assert len(reason) == len("  you dropped it: ") + knowledge.REASON
    assert "—" not in knowledge.one_line("a — b") and knowledge.one_line("a — b") == "a , b"


def test_an_accepted_intent_is_read_from_its_commit_once_the_drafts_are_gone(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    assert run_pilot(proj, tid, FakeDrafter(docs()))[0] == "ready"
    acc = accept(proj, tid)
    shutil.rmtree(acc["data"]["docs_moved_to"], ignore_errors=True)
    shutil.rmtree(lifecycle.task_dir(proj, tid), ignore_errors=True)
    assert lifecycle._read(proj, tid, "intent") == ""
    [block] = knowledge.summary(proj)
    assert "fixing the README install steps (docs)" in block.splitlines()[0]
    assert block.splitlines()[1] == "  outcomes: A new user on WSL can follow them."
