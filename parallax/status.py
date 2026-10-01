"""Task status, derived from the ledger. Never stored anywhere else."""
from __future__ import annotations



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
        elif kind == "task.redraft":
            t["status"] = "drafting"
        elif kind in ("build.finished", "check.finished"):
            t["status"] = d["status"]
        elif kind == "check.started":
            t["status"] = "checking"
        elif kind == "rework.started":
            t["status"] = "reworking"
        elif kind in ("task.rejected", "gate.rejected"):
            t["status"] = "rejected"  # out of the inbox; parallax task list still shows it
        elif kind in ("draft.recorded", "gate.approved") and t["status"] == "rejected":
            t["status"] = "open"  # an old gate rejected, then redrafted by hand (before M13)
        elif kind == "task.accepted":
            t["status"] = "merging" if d.get("merging") else "accepted"  # Accept and merge: merging from the click
        elif kind == "merge.started":
            t["status"] = "merging"
        elif kind == "merge.stopped":
            t["status"] = "accepted"  # the tests failed or it conflicted: nothing moved, the merge is yours
        elif kind == "merge.confirmed":
            t["status"] = "merged"
        elif kind == "task.stopped":
            t["status"] = "stopped"
        elif kind == "maker.finished" and d["status"] != "done" and t["status"] == "running":
            t["status"] = "maker failed"
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


BOARD = ("drafting", "building", "checking", "merging", "ready", "needs you", "done")
_TO_BOARD = {
    "drafting": "drafting", "running": "building", "reworking": "building", "built": "checking",
    "checking": "checking", "merging": "merging", "ready": "ready", "accepted": "done", "merged": "done", "rejected": "done",
    "stopped": "done", "closed": "done",
}


def board(status: str) -> str:
    """Where a task's status puts it on the board. Anything unknown waits on you, never hides."""
    return _TO_BOARD.get(status, "needs you")


def attempt(entries: list[dict], task_id: str) -> list[dict]:
    """A task's entries since its latest redraft: the current attempt.

    A reject at Ready starts a new attempt. Approvals, drafts, rework cycles and the budget count
    within an attempt; cost and touches still add up across the whole task."""
    mine = [e for e in entries if e["data"].get("task") == task_id]
    starts = [i for i, e in enumerate(mine) if e["kind"] == "task.redraft"]
    return mine[starts[-1]:] if starts else mine
