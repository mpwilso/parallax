"""Stop a task at any stage (real use: only `parallax stop` in a terminal, which ended every build at
once). One task's processes end, it's recorded as yours with the spend so far, and it waits on you:
resume from where it stopped, send it back, or drop it."""
import re
import subprocess
import sys
import threading
import time

import pytest

from fakes import FakeDrafter
from parallax import build, decide, lifecycle, pilot, show, status, stopping, views
from parallax.accept import Stopped, accept, merge_now
from parallax.cli import main
from parallax.core import Project
from test_accept import _with_merge_tests, git, ready
from test_check import approved
from test_lifecycle_gates import WANT, docs, make_key

SESSION = "import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)"  # a builder with an agent under it


@pytest.fixture
def spawned(monkeypatch):
    """Every spawn starts a stand-in builder in its own session, as the real one does. Returns them."""
    procs = []

    def spawn(argv, env, cwd, log):
        p = subprocess.Popen([sys.executable, "-c", SESSION], start_new_session=True)
        procs.append(p)
        return p.pid
    monkeypatch.setattr(build, "_spawn", spawn)
    yield procs
    for p in procs:  # a resumed stand-in still running: end it with the test
        if p.poll() is None:
            subprocess.run(["pkill", "-KILL", "-s", str(p.pid)], capture_output=True)
            p.wait(timeout=10)


def children(pid):
    out = subprocess.run(["pgrep", "-s", str(pid)], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out]


def stopped_entry(proj, tid):
    [e] = [e for e in proj.ledger.entries() if e["kind"] == "task.stopped" and e["data"]["task"] == tid]
    return e


def assert_stopped(proj, tid, proc, stage):
    assert children(proc.pid)  # the builder and its agent, running
    began = time.monotonic()
    assert stopping.stop(proj, tid, "stopped in parallax ui") == [tid]
    assert proc.wait(timeout=10) is not None and time.monotonic() - began < 6  # within a few seconds
    assert not children(proc.pid)  # its whole session, agent included
    e = stopped_entry(proj, tid)
    assert e["actor"] == "human" and e["reason"] == "stopped in parallax ui" and e["data"]["stage"] == stage
    assert e["data"]["spent_usd"] == pytest.approx(sum(x["data"].get("cost_usd") or 0 for x in status.attempt(proj.ledger.entries(), tid)))
    assert proj.task(tid)["status"] == "stopped" and status.board("stopped") == "needs you"
    dec = decide.decision(proj, tid)
    assert dec.kind == "stopped" and dec.recommend == "resume"
    assert [o.name for o in dec.options] == ["resume", "send back", "drop"]
    card = views.card(proj, tid)
    assert card["state"] == "needs you" and card["actions"]["kind"] == "decide"
    report = show.report(proj, tid)
    assert "Bottom line: Needs you: you stopped it." in report and f"You stopped it during {stage}, after $" in report
    assert f"Next: you run parallax decide {tid} resume." in report


# every stage -----------------------------------------------------------------------------------------------

def test_stopping_while_focus_drafts_then_resume_keeps_the_intent(repo, spawned):
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    drafter = FakeDrafter(docs())
    lifecycle.draft(proj, tid, ["intent"], drafter)  # the intent is written; the plan is under way
    assert views.card(proj, tid)["actions"] == {"kind": "running", "stop": True}
    assert_stopped(proj, tid, spawned[0], "Focus writing the plan")
    assert decide.apply(proj, tid, "resume") == f"drafting {tid} without you. it comes back to the inbox."
    pilot.draft_until_fit(proj, tid, drafter)  # what the resumed pilot does first
    asked = [re.search(r"docs/tasks/\w+/(\w+)\.md", r).group(1) for r in drafter.requests]
    assert asked == ["intent", "plan"]  # the intent before the stop, then only the plan: nothing redone


@pytest.mark.parametrize("stage,entries", [
    ("Reticle writing tests of what you asked", [("reticle.started", {})]),
    ("Maker building", [("maker.started", {"stage": "build"}), ("session.cost", {"cost_usd": 0.4})]),
    ("running the plan's tests", [("build.finished", {"status": "built"}), ("check.started", {})]),
    ("Field using the app", [("build.finished", {"status": "built"}), ("check.started", {}), ("uitest.started", {})]),
])
def test_stopping_at_each_build_stage(repo, spawned, stage, entries):
    proj, tid, wt = approved(repo)
    pid = build._spawn([], {}, repo, None)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=pid, mode="build")
    for kind, data in entries:
        proj.ledger.append(kind, "parallax", "", task=tid, **data)
    assert_stopped(proj, tid, spawned[0], stage)


def test_resume_picks_up_where_it_stopped(repo, spawned):
    proj, tid, wt = approved(repo)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=build._spawn([], {}, repo, None), mode="build")
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    stopping.stop(proj, tid)
    assert decide.apply(proj, tid, "resume") == f"building {tid} without you. it comes back to the inbox."
    assert [e["data"]["mode"] for e in proj.ledger.entries() if e["kind"] == "build.started"][-1] == "build"
    stopping.stop(proj, tid)
    proj.ledger.append("build.finished", "parallax", "", task=tid, status="built")  # built before the next stop
    proj.ledger.append("check.started", "parallax", "", task=tid)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=build._spawn([], {}, repo, None), mode="check")
    stopping.stop(proj, tid)
    assert decide.apply(proj, tid, "resume") == f"checking {tid} without you. it comes back to the inbox."  # no rebuild


def test_a_stopped_task_can_be_sent_back_or_dropped(repo, spawned, monkeypatch):
    proj, tid, wt = approved(repo)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=build._spawn([], {}, repo, None), mode="build")
    stopping.stop(proj, tid)
    with pytest.raises(Exception, match="needs a reason"):
        decide.apply(proj, tid, "send back")
    assert decide.apply(proj, tid, "send back", "smaller, please").startswith(f"redrafting {tid}")
    second = pilot.intake(proj, "another thing")["task"]
    stopping.stop(proj, second)
    assert decide.apply(proj, second, "drop", "not needed") == f"dropped {second}. it's out of the inbox."


# the pre-merge test run ------------------------------------------------------------------------------------

def test_stopping_the_pre_merge_test_run_never_moves_the_base_branch(repo, monkeypatch):
    from parallax import accept as acc_mod
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", acc_mod.run_tests)  # a real run, of sleep: no model, nothing to fake
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "sleep 30")
    accept(proj, tid, merging=True)
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    raised = []

    def merge():
        try:
            merge_now(Project(repo), tid)
        except Exception as err:  # noqa: BLE001  what the click's request would have got
            raised.append(err)
    t = threading.Thread(target=merge)
    t.start()
    pidfile = stopping.merge_pidfile(proj, tid)
    deadline = time.monotonic() + 20
    while not pidfile.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert pidfile.exists() and views.card(proj, tid)["actions"] == {"kind": "merging", "stop": True}
    began = time.monotonic()
    assert stopping.stop(proj, tid) == [tid]
    t.join(timeout=15)
    assert not t.is_alive() and time.monotonic() - began < 6
    assert len(raised) == 1 and isinstance(raised[0], Stopped)
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == head  # nothing moved
    e = stopped_entry(proj, tid)
    assert e["data"]["stage"] == "the pre-merge test run" and e["actor"] == "human"
    kinds = [x["kind"] for x in proj.ledger.entries() if x["data"].get("task") == tid]
    assert "merge.tested" not in kinds and "merge.stopped" not in kinds and "merge.clicked" not in kinds
    assert proj.task(tid)["status"] == "stopped" and decide.decision(proj, tid).kind == "stopped"
    # resume runs Accept and merge again
    policy = repo / "parallax.policy.toml"
    policy.write_text(policy.read_text().replace('test_command = "sleep 30"', 'test_command = "true"'))
    proj.reload_policy()
    assert decide.apply(proj, tid, "resume").startswith(f"merged {tid} into ")
    assert proj.task(tid)["status"] == "merged"


# parallax stop -------------------------------------------------------------------------------------------

def test_parallax_stop_task_stops_only_that_one_and_plain_stop_stops_the_rest(repo, spawned, monkeypatch, capsys):
    make_key()
    proj = Project.init(repo)
    one, two = pilot.intake(proj, "one thing")["task"], pilot.intake(proj, "another thing")["task"]
    monkeypatch.chdir(repo)
    assert main(["stop", one]) == 0
    assert capsys.readouterr().out == f"stopped {one}.\n"
    assert spawned[0].wait(timeout=10) is not None and spawned[1].poll() is None
    assert proj.task(two)["status"] == "drafting"
    assert main(["stop", one]) == 1
    assert f"task {one} isn't running" in capsys.readouterr().err
    assert main(["stop"]) == 0 and capsys.readouterr().out == f"stopped {two}.\n"
    assert spawned[1].wait(timeout=10) is not None


def test_parallax_stop_asks_once_in_a_terminal(repo, spawned, monkeypatch, capsys):
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, "one thing")["task"]
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "n")
    assert main(["stop", tid]) == 0 and capsys.readouterr().out == "nothing stopped.\n"
    assert asked == [f"stop {tid}, Focus writing the intent? its work so far is kept. [y/N] "]
    assert spawned[0].poll() is None
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    assert main(["stop", tid]) == 0 and capsys.readouterr().out == f"stopped {tid}.\n"
    spawned[0].wait(timeout=10)


def test_the_page_stops_one_task(repo, spawned):
    from parallax.ui import act
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, "one thing")["task"]
    assert act(proj, "/api/stop", {"task": tid})["message"] == f"stopped {tid}. it waits for you: resume, send back or drop."
    assert stopped_entry(proj, tid)["reason"] == "stopped in parallax ui"
