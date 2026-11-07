"""M16: first pass, rework cycles and waits in `parallax stats`, from constructed ledgers.

Each test writes a task's ledger entry by entry on a clock it moves by hand, so every duration is exact."""
from datetime import datetime, timedelta, timezone

import pytest

from parallax import ledger, measures, stats
from parallax.core import Project
from test_lifecycle_gates import make_key


class Clock:
    def __init__(self):
        self.at = datetime(2026, 11, 1, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()

    class Fixed(datetime):
        @classmethod
        def now(cls, tz=None):
            return c.at

    monkeypatch.setattr(ledger, "datetime", Fixed)
    return c


@pytest.fixture
def proj(repo, clock):
    make_key()
    return Project.init(repo)


def task(proj, goal="fix the typo", **kw):
    return proj.new_task(goal, "human", intent=True, **kw)["task"]


def log(proj, clock, tid, kind, actor="parallax", reason="", minutes=0, **data):
    """One entry for a task, minutes after the one before."""
    clock.at += timedelta(minutes=minutes)
    return proj.ledger.append(kind, actor, reason, task=tid, **data)


def ready(proj, clock, tid, minutes):
    log(proj, clock, tid, "pilot.started")
    log(proj, clock, tid, "check.started", minutes=minutes)
    log(proj, clock, tid, "check.finished", status="ready")


def rework(proj, clock, tid, minutes, cycle=1):
    log(proj, clock, tid, "rework.started", reason="tests: one failing", cycle=cycle, minutes=minutes)
    log(proj, clock, tid, "check.started", minutes=minutes)


def accept(proj, clock, tid, minutes):
    log(proj, clock, tid, "task.accepted", "human", minutes=minutes)


def lines(proj):
    return stats.report(proj)


# first pass ------------------------------------------------------------------------------------------

def test_first_pass_is_ready_on_its_own_with_no_rework_and_no_send_back(proj, clock):
    clean = task(proj)
    ready(proj, clock, clean, 10)
    accept(proj, clock, clean, 5)

    reworked = task(proj)
    log(proj, clock, reworked, "pilot.started")
    rework(proj, clock, reworked, 5)
    log(proj, clock, reworked, "check.finished", status="ready", minutes=5)
    accept(proj, clock, reworked, 5)

    sent_back = task(proj)
    ready(proj, clock, sent_back, 10)
    log(proj, clock, sent_back, "task.redraft", "human", "keep the old wording", minutes=5)
    ready(proj, clock, sent_back, 10)
    accept(proj, clock, sent_back, 5)

    dropped = task(proj)  # stuck before Ready, then dropped: finished, never Ready
    log(proj, clock, dropped, "pilot.started")
    log(proj, clock, dropped, "stuck.raised", reason="the sandbox didn't start", minutes=2)
    log(proj, clock, dropped, "task.rejected", "human", "not needed", minutes=3)

    still_open = task(proj)  # Ready, not decided: not finished, so out of every sum
    ready(proj, clock, still_open, 10)
    stopped = task(proj)  # you stopped it, and haven't said how it goes on: waits on you, not finished
    log(proj, clock, stopped, "pilot.started")
    log(proj, clock, stopped, "task.stopped", "human", "parallax stop", minutes=4)

    found = measures.measure(proj.ledger.entries())
    assert [found[t].first_pass for t in (clean, reworked, sent_back, dropped)] == [True, False, False, False]
    assert found[still_open].first_pass and not found[still_open].finished  # Ready, but not counted until finished
    assert not found[stopped].finished
    out = lines(proj)
    assert "first pass 1 of 4 finished (25%): ready with no rework and no send-back." in out
    rows = {line.split()[0]: line.split() for line in out[1:7]}
    assert rows[still_open][4:6] == ["-", "-"] and rows[stopped][4:6] == ["-", "-"]  # an open task's waits aren't over
    assert rows[stopped][-1] == "stopped"


def test_a_ready_you_overrode_is_not_a_first_pass(proj, clock):
    """Second Eye disagreed and you sided with Maker: Ready by your decision, not on its own."""
    tid = task(proj)
    log(proj, clock, tid, "pilot.started")
    raised = log(proj, clock, tid, "disagreement.raised", reason="a blocker in README.md", stage="check", minutes=10)
    proj.ledger.append("decision.resolved", "human", "the blocker is wrong", decision=raised["id"], task=tid,
                       about="disagreement.raised", stage="check", outcome="approved")
    accept(proj, clock, tid, 5)
    m = measures.measure(proj.ledger.entries())[tid]
    assert m.finished and not m.first_pass and m.waited == 5 * 60  # Ready after your call waits on you too


# rework cycles -------------------------------------------------------------------------------------------

def test_rework_cycles_give_the_median_the_max_and_the_tasks_at_the_cap(proj, clock):
    none = task(proj)
    ready(proj, clock, none, 5)
    accept(proj, clock, none, 1)

    one = task(proj)
    log(proj, clock, one, "pilot.started")
    rework(proj, clock, one, 2)
    log(proj, clock, one, "check.finished", status="ready")
    accept(proj, clock, one, 1)

    capped = task(proj)  # three cycles, then the 4th fail came to you; you accepted the risk
    log(proj, clock, capped, "pilot.started")
    for n in (1, 2, 3):
        rework(proj, clock, capped, 2, cycle=n)
    raised = log(proj, clock, capped, "disagreement.raised", reason="the check still fails after 3 rework cycles: tests",
                 stage="check", rework_cap=3)
    log(proj, clock, capped, "decision.resolved", "human", "fine as it is", minutes=10, decision=raised["id"],
        about="disagreement.raised", stage="check", outcome="approved")
    accept(proj, clock, capped, 1)

    older = task(proj)  # an entry from before rework_cap was recorded: its reason says it
    log(proj, clock, older, "pilot.started")
    for n in (1, 2, 3):
        rework(proj, clock, older, 2, cycle=n)
    log(proj, clock, older, "disagreement.raised", reason="the check still fails after 3 rework cycles: tests",
        stage="check")
    log(proj, clock, older, "task.rejected", "human", "not worth it", minutes=10)

    going = task(proj)  # an open task's cycles show on its row, and stay out of the median
    log(proj, clock, going, "pilot.started")
    for n in (1, 2, 3, 4):
        rework(proj, clock, going, 1, cycle=n)

    out = lines(proj)
    assert "rework cycles per finished task: median 2, max 3. 2 hit the rework cap." in out
    rows = {line.split()[0]: line.split() for line in out[1:6]}
    assert [rows[t][3] for t in (none, one, capped, older, going)] == ["0", "1", "3", "3", "4"]


def test_a_rework_cycle_count_can_be_a_half(proj, clock):
    for cycles in (0, 1):
        tid = task(proj)
        log(proj, clock, tid, "pilot.started")
        for n in range(cycles):
            rework(proj, clock, tid, 1, cycle=n + 1)
        log(proj, clock, tid, "check.finished", status="ready")
        accept(proj, clock, tid, 1)
    assert "rework cycles per finished task: median 0.5, max 1. 0 hit the rework cap." in lines(proj)


# waiting on you ------------------------------------------------------------------------------------------

def test_wait_runs_from_a_decision_needed_or_ready_to_your_next_decision(proj, clock):
    quick = task(proj)
    log(proj, clock, quick, "pilot.started")
    log(proj, clock, quick, "review.requested", reason="over auto_launch_usd", minutes=4)  # Decision needed
    log(proj, clock, quick, "gate.approved", "human", minutes=10, gate="plan", files={})  # you launch it: 10m waited
    log(proj, clock, quick, "maker.started", minutes=1)
    log(proj, clock, quick, "check.finished", status="ready", minutes=6)
    accept(proj, clock, quick, 20)  # Ready for 20m: 30m waited in all, 11m worked

    slow = task(proj)
    ready(proj, clock, slow, 3)
    accept(proj, clock, slow, 125)

    stopped = task(proj)  # you stopped it; it waited on you until you dropped it
    log(proj, clock, stopped, "pilot.started")
    log(proj, clock, stopped, "task.stopped", "human", "parallax stop", minutes=7)
    log(proj, clock, stopped, "task.rejected", "human", "not now", minutes=60)

    found = measures.measure(proj.ledger.entries())
    assert (found[quick].waited, found[quick].worked) == (30 * 60, 11 * 60)
    assert (found[slow].waited, found[slow].worked) == (125 * 60, 3 * 60)
    assert (found[stopped].waited, found[stopped].worked) == (60 * 60, 7 * 60)
    out = lines(proj)
    assert f"waiting on you: median 1h 0m, longest 2h 5m ({slow}). working without you: median 7m." in out
    row = next(line.split() for line in out if line.startswith(quick))
    assert row[4:6] == ["30m", "11m"]


def test_a_wait_ends_when_code_carries_out_your_answer_from_another_card(proj, clock):
    """One trust answered two cards: the second task's wait ends at that answer, not at its accept."""
    mine, other = task(proj), task(proj)
    for t in (mine, other):
        log(proj, clock, t, "pilot.started")
    asked = [log(proj, clock, t, "stuck.raised", reason="trust these commands?", minutes=2, commands_sha="abc")
             for t in (mine, other)]
    answer = log(proj, clock, mine, "decision.resolved", "human", "trust", minutes=15, decision=asked[0]["id"],
                 about="stuck.raised", outcome="approved")
    log(proj, clock, other, "decision.resolved", "parallax", "answered by your trust", decision=asked[1]["id"],
        about="stuck.raised", outcome="approved", by=answer["id"])
    for t in (mine, other):
        ready(proj, clock, t, 5)
        accept(proj, clock, t, 1)
    found = measures.measure(proj.ledger.entries())
    assert (found[mine].waited, found[other].waited) == (18 * 60, 16 * 60)  # 17m and 15m, then 1m at Ready


def test_a_task_that_never_waited_waited_nothing(proj, clock):
    tid = task(proj)
    log(proj, clock, tid, "pilot.started")
    log(proj, clock, tid, "task.rejected", "human", "wrong repo", minutes=3)  # dropped while it worked
    m = measures.measure(proj.ledger.entries())[tid]
    assert (m.waited, m.worked, m.first_pass) == (0, 3 * 60, False)


def test_spans_are_short():
    assert [measures.span(s) for s in (None, 0, 59, 60, 3599, 3600, 7500, 86400 + 4 * 3600)] == \
        ["-", "0s", "59s", "1m", "59m", "1h 0m", "2h 5m", "1d 4h"]
    assert measures.union([(0, 10), (5, 20), (30, 40), (32, 35)]) == 30


# a split request -----------------------------------------------------------------------------------------

def test_a_split_request_counts_its_parts_and_its_waits_once(proj, clock):
    lead = task(proj, "fix both typos", lead=True)
    proj.ledger.append("request.split", "parallax", "", request=lead, task=lead,
                       parts=[{"n": 1, "goal": "fix the README typo", "scope": ["README.md"], "after": None},
                              {"n": 2, "goal": "fix the notes typo", "scope": ["NOTES.md"], "after": None}])
    second = task(proj, "fix the notes typo", request=lead, part=2)
    for t in (lead, second):
        log(proj, clock, t, "pilot.started")
    log(proj, clock, lead, "check.finished", status="ready", minutes=10)  # Ready at 10m
    log(proj, clock, second, "rework.started", reason="tests: one failing", cycle=1, minutes=5)
    log(proj, clock, second, "check.finished", status="ready", minutes=5)  # Ready at 20m
    accept(proj, clock, lead, 10)  # lead waited 20m, from 10m to 30m
    accept(proj, clock, second, 10)  # second waited 20m, from 20m to 40m

    out = lines(proj)
    assert f"{'request':<8}{'parts':>8}{'touches':>8}{'cost':>9}{'first':>8}{'rework':>8}{'waited':>8}{'worked':>8}" in out
    assert f"{lead:<8}{2:>8}{2:>8}{'$0.00':>9}{'1/2':>8}{1:>8}{'30m':>8}{'10m':>8}" in out  # 10m to 40m waited, once
    assert "first pass 1 of 2 finished (50%): ready with no rework and no send-back." in out
    assert out[0].split() == ["task", "touches", "cost", "rework", "waited", "worked", "status"]  # the CLI's: test_pilot


def test_a_split_request_with_a_part_still_open_shows_no_wait_yet(proj, clock):
    lead = task(proj, "fix both typos", lead=True)
    proj.ledger.append("request.split", "parallax", "", request=lead, task=lead,
                       parts=[{"n": 1, "goal": "a", "scope": ["README.md"], "after": None},
                              {"n": 2, "goal": "b", "scope": ["README.md"], "after": 1}])  # part 2 isn't made yet
    ready(proj, clock, lead, 5)
    accept(proj, clock, lead, 5)
    row = next(line for line in lines(proj)[1:] if line.startswith(lead) and "/" in line)
    assert row.split()[4:] == ["1/1", "0", "-", "-"]


def test_no_finished_task_means_no_measures_yet(proj, clock):
    tid = task(proj)
    ready(proj, clock, tid, 5)
    out = lines(proj)
    assert out[-1] == "no finished tasks yet. target 1 touch per task." and not any("first pass" in x for x in out)
