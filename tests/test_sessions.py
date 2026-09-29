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
from test_m8 import WANT, docs, make_key


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
