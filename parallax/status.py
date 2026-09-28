"""Task status, derived from the ledger. Never stored anywhere else."""
from __future__ import annotations

from .agents.base import AGREE


def derive(entries: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for e in entries:
        kind, d = e["kind"], e["data"]
        if kind == "task.created":
            out[d["task"]] = {"goal": e["reason"], "status": "open", "profile": "default",
                              "plan": False, **d, "last": e["ts"]}
            continue
        t = out.get(d.get("task"))
        if t is None:
            continue
        t["last"] = e["ts"]
        if kind == "task.closed":
            t["status"] = d["outcome"]
        elif kind == "task.queued":
            t["status"] = "queued"
        elif kind == "run.launched":
            t["status"] = "launched"
        elif kind == "maker.started":
            t["status"] = "running"
        elif kind == "maker.finished" and d["status"] != "done" and t["status"] == "running":
            t["status"] = "maker failed"
        elif kind == "report.recorded":
            t["status"] = "reported"
        elif kind == "verdict.recorded" and d["stage"] == "diff" and d["verdict"] in AGREE:
            t["status"] = "ready"
        elif kind == "disagreement.raised":
            t["status"] = "disputed"
        elif kind == "stuck.raised":
            t["status"] = "stuck"
        elif kind == "decision.resolved":
            about, approved = d.get("about"), d["outcome"] == "approved"
            if about == "disagreement.raised":
                t["status"] = ("plan approved" if d.get("stage") == "plan" else "ready") if approved else "needs work"
            elif about == "stuck.raised":
                t["status"] = "open" if approved else "closed"
    return out
