"""Every agent session is in the ledger, even one that ends early.

The adapter reports a session as soon as it starts (agent.started: which agent, which task, the
session id, the process) and when it ends normally (agent.ended; its cost is already on the entry
the caller records). A session that started and never ended, whose process is gone or is this
process finishing, ended early: cut off, crashed, timed out or killed. It gets agent.ended_early,
with the cost its Claude Code transcript last recorded, marked partial, or "unknown" without one.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .core import Project


def transcripts() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"


def known_cost(session: str) -> float | None:
    """The session's cost as its transcript last recorded it, or None."""
    cost = None
    for path in transcripts().glob(f"*/{session}.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    e = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(e, dict) and isinstance(e.get("totalCostUSD"), (int, float)):
                    cost = float(e["totalCostUSD"])
        except OSError:
            continue
    return cost


class Recorder:
    """What the adapter calls, around the background work for one task."""

    def __init__(self, project: Project, task_id: str):
        self.project, self.task_id = project, task_id

    def started(self, session: str, cwd: str, agent: str) -> None:
        self.project.ledger.append("agent.started", "parallax", "", task=self.task_id, agent=agent, session=session,
                                   cwd=str(cwd), pid=os.getpid())

    def ended(self, session: str, agent: str, cost: float | None) -> None:
        # no cost_usd here: the caller's own entry carries it, and costs add up every cost_usd
        self.project.ledger.append("agent.ended", "parallax", "", task=self.task_id, agent=agent, session=session,
                                   cost_seen=cost)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def reconcile(project: Project, task_id: str | None = None, finishing: bool = False) -> list[dict]:
    """Record every session that ended early. finishing: this process is done with task_id, so its
    own sessions that never ended ended early too. Returns the entries it added."""
    entries = project.ledger.entries()
    closed = {e["data"].get("session") for e in entries if e["kind"] in ("agent.ended", "agent.ended_early")}
    added = []
    for e in entries:
        d = e["data"]
        if e["kind"] != "agent.started" or d.get("session") in closed or (task_id and d.get("task") != task_id):
            continue
        mine = d.get("pid") == os.getpid()
        if not ((mine and finishing) or (not mine and not _alive(int(d.get("pid") or 0)))):
            continue  # still running somewhere
        cost = known_cost(d["session"])
        why = (f"the {d['agent']} session ended early; ${cost:.2f} from its transcript, partial" if cost is not None
               else f"the {d['agent']} session ended early; its cost is unknown")
        extra = {"cost_usd": cost} if cost is not None else {}
        added.append(project.ledger.append("agent.ended_early", "parallax", why, task=d.get("task"), agent=d["agent"],
                                           session=d["session"], partial=True,
                                           source="transcript" if cost is not None else "unknown", **extra))
        closed.add(d["session"])
    return added
