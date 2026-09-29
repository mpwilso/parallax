"""cd8307: a drafter session cut off mid-run left no cost in the ledger. Now every session is
recorded from its start, and one that ends early is found, costed from its transcript, and marked."""
import asyncio
import json
import subprocess
import sys

import pytest

from fakes import FakeDrafter
from parallax import build, costs, pilot, sessions, stats
from parallax.agents import base
from parallax.cli import main
from parallax.core import Project
from test_lifecycle_gates import WANT, docs, make_key


@pytest.fixture
def proj(repo, monkeypatch, tmp_path):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))  # never the real ~/.claude
    return Project.init(repo)


def dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def transcript(tmp_path, session, cost):
    folder = tmp_path / "claude" / "projects" / "-some-worktree"
    folder.mkdir(parents=True, exist_ok=True)
    lines = [{"type": "assistant", "message": {"usage": {"output_tokens": 10}}}]
    if cost is not None:
        lines.append({"type": "cost-state", "totalCostUSD": cost})
    (folder / f"{session}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")


def early(proj):
    return [e for e in proj.ledger.entries() if e["kind"] == "agent.ended_early"]


def test_a_killed_session_is_recorded_with_its_partial_cost(proj, tmp_path):
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("agent.started", "parallax", "", task=tid, agent="drafter", session="s1", cwd="/wt", pid=dead_pid())
    transcript(tmp_path, "s1", 0.2205825)
    before = costs.spent(proj, tid)
    [e] = sessions.reconcile(proj)
    d = e["data"]
    assert (d["task"], d["agent"], d["partial"], d["source"], d["cost_usd"]) == (tid, "drafter", True, "transcript", 0.2205825)
    assert "ended early" in e["reason"] and "partial" in e["reason"]
    assert costs.spent(proj, tid) == pytest.approx(before + 0.2205825, abs=1e-4)  # it counts against the cap
    assert sessions.reconcile(proj) == []  # once


def test_with_no_transcript_the_cost_is_unknown_but_the_session_is_recorded(proj):
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("agent.started", "parallax", "", task=tid, agent="maker", session="s2", cwd="/wt", pid=dead_pid())
    [e] = sessions.reconcile(proj)
    assert e["data"]["source"] == "unknown" and "cost_usd" not in e["data"] and "unknown" in e["reason"]


def test_a_session_that_ended_or_is_still_running_is_left_alone(proj):
    tid = pilot.intake(proj, WANT)["task"]
    gone = dead_pid()
    proj.ledger.append("agent.started", "parallax", "", task=tid, agent="checker", session="done", cwd="/", pid=gone)
    proj.ledger.append("agent.ended", "parallax", "", task=tid, agent="checker", session="done", cost_seen=0.03)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        proj.ledger.append("agent.started", "parallax", "", task=tid, agent="maker", session="busy", cwd="/", pid=live.pid)
        assert sessions.reconcile(proj) == []
    finally:
        live.kill()


def test_a_session_this_process_never_finished_is_recorded_when_the_run_ends(proj, tmp_path):
    """Crashed or timed out inside a background run: the run's own end records it."""
    tid = pilot.intake(proj, WANT)["task"]

    class Crashing(FakeDrafter):
        def run(self, goal, cwd, permission_fn, stage="build", env=None):
            base.session_started("s3", str(cwd), "drafter")  # what the adapter reports first
            raise TimeoutError("the drafter timed out")
    transcript(tmp_path, "s3", 0.41)
    assert build.run_mode(proj, tid, "pilot", Crashing(docs()), None, None) == "stuck"
    [e] = early(proj)
    assert (e["data"]["agent"], e["data"]["cost_usd"], e["data"]["partial"]) == ("drafter", 0.41, True)
    assert base.SESSIONS is None


def test_the_adapter_reports_each_sessions_start_and_end(monkeypatch):
    from parallax.agents import claude
    seen = []

    class Recorder:
        def started(self, *a):
            seen.append(("started", *a))

        def ended(self, *a):
            seen.append(("ended", *a))

    class Init:
        session_id, data = None, {"session_id": "abc"}

    class Result:
        session_id, total_cost_usd = "abc", 0.12

    class Client:
        def __init__(self, options): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def query(self, prompt): pass

        async def receive_response(self):
            for m in (Init(), Result()):
                yield m

    class SDK:
        ClaudeSDKClient, ResultMessage = Client, Result

    class Options:
        cwd = "/wt"
    monkeypatch.setattr(base, "SESSIONS", Recorder())
    assert asyncio.run(claude._final_result(SDK, Options(), "go", "maker")).total_cost_usd == 0.12
    assert seen == [("started", "abc", "/wt", "maker"), ("ended", "abc", "maker", 0.12)]


def test_stats_counts_partial_costs_and_says_how_many(proj, tmp_path, monkeypatch, capsys):
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("agent.started", "parallax", "", task=tid, agent="drafter", session="s4", cwd="/", pid=dead_pid())
    proj.ledger.append("agent.started", "parallax", "", task=tid, agent="maker", session="s5", cwd="/", pid=dead_pid())
    transcript(tmp_path, "s4", 1.5)
    monkeypatch.chdir(proj.root)
    main(["stats"])  # every command does the housekeeping, stats included
    out = capsys.readouterr().out
    assert len(early(proj)) == 2 and stats.partial(proj)[tid]
    assert f"{tid}" in out and "$1.50" in out and "(2 partial)" in out
    assert "2 agent sessions ended early. their costs are counted as partial; 1 unknown, counted as $0." in out


CHILD = """
import random, sys, time
from pathlib import Path
from parallax import sessions
from parallax.core import Project
proj = Project(Path(sys.argv[1]))
rec = sessions.Recorder(proj, sys.argv[2])
rec.started(sys.argv[3], "/wt", "maker")
time.sleep(random.uniform(0, 0.05))
if sys.argv[4] == "end":
    rec.ended(sys.argv[3], "maker", 0.1)  # a normal end, then the process exits at once
"""


def test_a_normal_end_is_never_recorded_as_partial_whatever_the_timing(proj):
    """Race 20 sessions that end normally and exit against a reconcile running the whole time."""
    tid = pilot.intake(proj, WANT)["task"]
    kids = [subprocess.Popen([sys.executable, "-c", CHILD, str(proj.root), tid, f"ok{i}", "end"]) for i in range(20)]
    control = subprocess.Popen([sys.executable, "-c", CHILD, str(proj.root), tid, "cut", "cut"])  # ends early
    while any(k.poll() is None for k in [*kids, control]):
        sessions.reconcile(proj)
    sessions.reconcile(proj)
    flagged = {e["data"]["session"] for e in early(proj)}
    assert flagged == {"cut"}  # the loop was live: it caught the one that never ended
    ended = {e["data"]["session"] for e in proj.ledger.entries() if e["kind"] == "agent.ended"}
    assert ended == {f"ok{i}" for i in range(20)}
    ok, _ = proj.ledger.verify()
    assert ok  # the chain holds under the concurrent writes


def test_a_reader_never_sees_half_an_entry(proj):
    pilot.intake(proj, WANT)
    n = len(proj.ledger.entries())
    with proj.ledger.path.open("a") as f:
        f.write('{"id": "half", "kind": "agent.st')  # a writer caught mid-line
        f.flush()
        assert len(proj.ledger.entries()) == n


def test_no_agent_can_write_a_transcript_parallax_reads_cost_from(proj, tmp_path):
    from pathlib import Path
    from parallax import gate, lifecycle, uitest
    from parallax.sandbox import rules
    root = sessions.transcripts()
    (root / "-wt").mkdir(parents=True)
    target = root / "-wt" / "s9.jsonl"
    tid = pilot.intake(proj, WANT)["task"]
    pilot.draft_until_fit(proj, tid, FakeDrafter(docs()))
    lifecycle.approve(proj, tid, rule="test")
    wt = Path(proj.task(tid)["worktree"])
    # the maker's sandbox: writes only in the worktree, the transcripts named in denyWrite, home unreadable
    p = build.prepare(proj, tid, setup=False, launching=False)
    assert p.rules.allow_write == [str(wt)] and str(root) in p.rules.deny_write
    assert not any(Path(str(root)).is_relative_to(w) for w in p.rules.allow_write)
    assert any(f'Edit(/{root}' in d for d in p.rules.claude_settings()["permissions"]["deny"])
    # the maker's tools: the gate refuses the write
    fn = gate.make_permission_fn(proj, tid, wt, scope=p.scope)
    assert not fn("fs.write", str(target), [str(target)]).allowed
    # drafters are read-only; the checker has no tools; the UI tester writes only in its own folder
    assert not gate.make_permission_fn(proj, tid, wt, read_only=True)("fs.write", str(target), [str(target)]).allowed
    assert not uitest.allowed("Write", {"file_path": str(target)}, tmp_path / "work")
    # the sandbox the UI tester's browser and the app share
    r = rules(wt, [], git_dir=None, venv=None, reads=[], domains=[])
    assert str(root) in r.deny_write and not any(Path(str(root)).is_relative_to(w) for w in r.allow_write)
    assert not target.exists()
