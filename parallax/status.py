"""Task status, derived from the ledger. Never stored anywhere else."""
from __future__ import annotations

from .agents.base import AGREE


def derive(entries: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        kind, d = e["kind"], e["data"]
        if kind == "task.created":
            out[d["task"]] = {"goal": e["reason"], "status": "open", "plan": False, **d, "last": e["ts"]}
            continue
        t = out.get(d.get("task"))
        if t is None:
            continue
        t["last"] = e["ts"]
        if d.get("cost_usd"):
            t["cost_usd"] = t.get("cost_usd", 0.0) + d["cost_usd"]
        if kind == "task.closed":
            t["status"] = d["outcome"]
        elif kind in ("build.started", "maker.started"):
            t["status"] = "drafting" if d.get("mode") == "pilot" else "running"
        elif kind == "pilot.started":
            t["status"] = "drafting"
        elif kind == "review.requested":
            t["status"] = "needs you"
        elif kind in ("build.finished", "check.finished"):
            t["status"] = d["status"]
        elif kind == "check.started":
            t["status"] = "checking"
        elif kind == "rework.started":
            t["status"] = "reworking"
        elif kind == "task.rejected":
            t["status"] = "rejected"
        elif kind == "task.accepted":
            t["status"] = "accepted"
        elif kind == "merge.confirmed":
            t["status"] = "merged"
        elif kind == "task.stopped":
            t["status"] = "stopped"
        elif kind == "maker.finished" and d["status"] != "done" and t["status"] == "running":
            t["status"] = "maker failed"
        elif kind == "verdict.recorded" and d["stage"] == "diff" and d["verdict"] in AGREE:
            t["status"] = "ready"
        elif kind == "disagreement.raised":
            t["status"] = "disputed"
        elif kind == "stuck.raised":
            t["status"] = "stuck"
        elif kind == "decision.resolved":
            about, approved = d.get("about"), d["outcome"] == "approved"
            if about == "disagreement.raised":
                won = {"plan": "plan approved", "scope": "risk accepted", "conflict": "risk accepted"}.get(d.get("stage"), "ready")
                t["status"] = won if approved else "needs work"
            elif about == "stuck.raised":
                t["status"] = "open" if approved else "closed"
    return out


BOARD = ("drafting", "building", "checking", "ready", "needs you", "done")
_TO_BOARD = {
    "drafting": "drafting", "running": "building", "reworking": "building", "built": "checking",
    "checking": "checking", "ready": "ready", "accepted": "done", "merged": "done", "rejected": "done",
    "stopped": "done", "closed": "done",
}


def board(status: str) -> str:
    """Where a task's status puts it on the board. Anything unknown waits on you, never hides."""
    return _TO_BOARD.get(status, "needs you")
