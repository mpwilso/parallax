import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import parallax.cli as cli
from fakes import FakeChecker, FakeConductor, ScriptedAgent
from parallax.agents.base import Proposal, Recommendation
from parallax.core import POLICY_FILE, ParallaxError, Project
from parallax.inbox import resolve_item, split_goal
from parallax.mission import MISSION_FILE, TEMPLATE, parse
from parallax.pulse import pulse
from parallax.runner import run_task

MISSION = """\
# Mission

## who
A careful maintainer of a tiny calculator library.

## what
- flag any task stuck for long

## how
Keep diffs small. Never touch the tests without asking.
"""


def setup(repo: Path, mission: str | None = MISSION, limits: str = "") -> Project:
    (repo / POLICY_FILE).write_text(f'[actions]\n"fs.read" = "allow"\n"fs.write" = "allow"\n{limits}')
    if mission is not None:
        (repo / MISSION_FILE).write_text(mission)
    return Project.init(repo)


class Launcher:
    def __init__(self):
        self.started: list[str] = []

    def __call__(self, project, task_id):
        self.started.append(task_id)
        return 0


def pulse_records(proj: Project) -> list[dict]:
    return [e for e in proj.ledger.entries() if e["kind"] == "pulse.recorded"]


# mission ---------------------------------------------------------------------

def test_mission_template_is_not_a_mission(repo):
    assert parse(TEMPLATE) is None  # comments only
    m = parse(MISSION)
    assert m.who.startswith("A careful") and "Keep diffs small" in m.how
    Project.init(repo)
    assert (repo / MISSION_FILE).read_text() == TEMPLATE  # init writes the template


def test_maker_gets_the_laws_checker_does_not(repo):
    proj = setup(repo)
    tid = proj.new_task("add mul()")["task"]
    agent, checker = ScriptedAgent(), FakeChecker()
    run_task(proj, tid, agent, checker)
    assert "Keep diffs small" in agent.goals[-1][1]
    assert "Keep diffs small" not in checker.calls[-1][0] + checker.calls[-1][1]


# pulse checks and launches ---------------------------------------------------------

def test_quiet_pulse_records_no_finding(repo):
    proj = setup(repo, mission=None)
    res = pulse(proj, FakeConductor(), launch=Launcher())
    assert res["findings"] == [] and res["conductor"].startswith("skipped: no mission")
    [rec] = pulse_records(proj)
    assert rec["reason"] == "no finding"


def test_pulse_launches_queued_up_to_the_cap_without_doubling(repo):
    proj = setup(repo, limits="[limits]\nmax_parallel = 2")
    ids = [proj.new_task(f"t{i}", queue=True)["task"] for i in range(3)]
    launch = Launcher()
    assert pulse(proj, launch=launch)["launched"] == ids[:2]
    assert pulse(proj, launch=launch)["launched"] == []  # both slots busy, nothing launched twice
    assert launch.started == ids[:2]
    assert proj.task(ids[0])["status"] == "launched" and proj.task(ids[2])["status"] == "queued"


def test_stale_run_is_flagged_once(repo):
    proj = setup(repo, limits="[limits]\nstale_minutes = 5")
    tid = proj.new_task("x", queue=True)["task"]
    pulse(proj, launch=Launcher())
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    conductor = FakeConductor()
    first = pulse(proj, conductor, launch=Launcher(), now=later)
    assert any(tid in f and "stuck" in f for f in first["findings"])
    reported = conductor.reviews[0][1].split("tasks:")[0]
    assert f"task {tid} flagged stuck" in reported  # the conductor is told, so it won't repeat it
    assert pulse(proj, launch=Launcher(), now=later)["findings"] == []  # already in your inbox
    [item] = proj.inbox()
    assert item["kind"] == "stuck.raised" and item["data"]["task"] == tid


def test_task_waiting_on_you_is_not_stale(repo):
    proj = setup(repo, limits='"git.commit" = "ask"\n[limits]\nstale_minutes = 5')
    tid = proj.new_task("x", queue=True)["task"]
    pulse(proj, launch=Launcher())
    proj.check(tid, "git.commit")
    later = datetime.now(timezone.utc) + timedelta(minutes=10)
    assert pulse(proj, launch=Launcher(), now=later)["findings"] == []


# the conductor ---------------------------------------------------------------------

def test_conductor_proposes_and_recommends_but_decides_nothing(repo):
    proj = setup(repo, limits='"git.commit" = "ask"')
    tid = proj.new_task("x")["task"]
    pending = proj.check(tid, "git.commit")["entry"]
    conductor = FakeConductor(
        findings=["add() has no tests"],
        proposals=[Proposal("add tests for add()", "no coverage", "default", True)],
        recommend=lambda snap: [Recommendation(pending["id"], "approve", "commit is small"),
                                Recommendation("nonexistent", "approve", "ignored")],
    )
    res = pulse(proj, conductor, launch=Launcher())
    assert res["findings"] == ["add() has no tests"]

    mission, snap = conductor.reviews[0]
    assert "careful maintainer" in mission
    assert pending["id"] in snap and "git.commit" in snap

    kinds = [e["kind"] for e in proj.inbox()]
    assert kinds == ["decision.requested", "proposal.raised"]
    recs = [e for e in proj.ledger.entries() if e["kind"] == "recommendation.recorded"]
    assert [r["data"]["item"] for r in recs] == [pending["id"]]
    assert proj.decision_outcome(pending["id"]) is None  # recommended, not applied


def test_conductor_failure_is_a_finding(repo):
    proj = setup(repo)
    res = pulse(proj, FakeConductor(error=True), launch=Launcher())
    assert res["conductor"] == "failed"
    assert res["findings"][0].startswith("conductor failed")
    assert pulse_records(proj)[-1]["reason"].startswith("conductor failed")


def test_goal_becomes_proposals_that_need_a_yes(repo):
    proj = setup(repo)
    conductor = FakeConductor(split=[Proposal("add mul()", "asked for", "default", False),
                                     Proposal("audit error handling", "asked for", "readonly", False),
                                     Proposal("weird", "x", "superuser", False)])
    entries = split_goal(proj, conductor, "grow the calculator")
    assert conductor.splits[0][1] == "grow the calculator"
    assert len(entries) == 3 and proj.tasks() == {}  # nothing created yet
    assert entries[2]["data"]["profile"] == "default" and "unknown profile" in entries[2]["data"]["why"]

    _, task = resolve_item(proj, entries[0]["id"], True, "yes, small")
    assert task["status"] == "queued" and task["goal"] == "add mul()" and task["proposal"] == entries[0]["id"]
    _, task = resolve_item(proj, entries[1]["id"], True, "worth a look")
    assert task["profile"] == "readonly"
    _, none = resolve_item(proj, entries[2]["id"], False, "no")
    assert none is None and len(proj.tasks()) == 2

    snap_conductor = FakeConductor()
    pulse(proj, snap_conductor, launch=Launcher())
    assert "weird" in snap_conductor.reviews[0][1].split("rejected proposals")[1]  # told not to re-propose


def test_goal_failure_is_raised_and_recorded(repo):
    proj = setup(repo)
    with pytest.raises(ParallaxError, match="conductor failed"):
        split_goal(proj, FakeConductor(error=True), "anything")
    assert [e["kind"] for e in proj.ledger.entries()][-2:] == ["goal.received", "goal.failed"]


# cli ---------------------------------------------------------------------------------

def test_cli_batched_inbox_and_multi_approve(repo, monkeypatch, capsys):
    proj = setup(repo, limits='"git.commit" = "ask"')
    tid = proj.new_task("fix add()")["task"]
    a = proj.check(tid, "git.commit", "first")["entry"]["id"]
    b = proj.check(tid, "git.commit", "second")["entry"]["id"]
    fake = FakeConductor(split=[Proposal("add mul()", "asked for", "default", False)],
                         recommend=lambda snap: [Recommendation(a, "approve", "tiny commit")])
    monkeypatch.setattr(cli, "_conductor", lambda model: fake)
    monkeypatch.chdir(repo)

    assert cli.main(["goal", "grow it"]) == 0
    prop = re.search(r"^(\w{8})  new task", capsys.readouterr().out, re.M).group(1)
    assert cli.main(["pulse"]) == 0
    capsys.readouterr()

    assert cli.main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert out.index(f"task {tid}") < out.index("proposals")  # task groups first, proposals last
    assert "conductor recommends approve: tiny commit" in out
    assert "why: asked for" in out

    assert cli.main(["approve", a, b, prop, "--reason", "reviewed together"]) == 0
    out = capsys.readouterr().out
    assert out.count("approved") == 3 and "created and queued" in out
    assert proj.inbox() == []
    assert all(e["reason"] == "reviewed together" for e in proj.ledger.entries() if e["kind"] == "decision.resolved")
    assert cli.main(["reject", "nope0000", "--reason", "x"]) == 1
    assert proj.ledger.verify()[0]
