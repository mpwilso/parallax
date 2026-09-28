"""What the visual inbox shows, built from the ledger. Plain data, no HTTP, so it's testable.

Every item carries the context a human needs to decide it, and the plain-word labels for the
two choices, which depend on the kind of item.
"""
from __future__ import annotations

from . import inbox
from .core import Project

MAX_DIFF = 200_000

CHOICES = {
    "decision.requested": ("Allow", "Refuse"),
    "disagreement.raised": ("Side with the maker", "Side with the checker"),
    "stuck.raised": ("Let it run again", "Close the task"),
    "proposal.raised": ("Create the task", "Drop it"),
    "promotion.raised": ("Promote", "Keep asking"),
    "law.raised": ("Adopt the law", "Reject it"),
}
KIND_NAMES = {
    "decision.requested": "permission", "disagreement.raised": "disagreement", "stuck.raised": "stuck",
    "proposal.raised": "proposal", "promotion.raised": "promotion", "law.raised": "law",
}
VERBS = {"fs.write": "wants to write", "fs.read": "wants to read", "shell.run": "wants to run",
         "net.fetch": "wants to fetch", "git.commit": "wants to commit", "git.push": "wants to push"}


def _one_line(text: str, width: int = 140) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[:width - 3] + "..."


def _diff(project: Project, task_id: str) -> str:
    try:
        diff = project.diff(task_id)
    except Exception as err:  # a missing worktree shouldn't break the page
        return f"(couldn't read the change: {err})"
    if len(diff) > MAX_DIFF:
        return diff[:MAX_DIFF] + f"\n\n(change truncated here. see all of it with `parallax task diff {task_id}`)"
    return diff


def headline(e: dict) -> str:
    d = e["data"]
    kind = e["kind"]
    if kind == "decision.requested":
        target = d.get("key") or e["reason"]
        return _one_line(f"{VERBS.get(d['action'], 'wants ' + d['action'])} {target}".strip())
    if kind == "disagreement.raised":
        return {"plan": "checker disagrees with the plan", "guard": "touched a protected file"}.get(
            d.get("stage"), "checker disagrees with the change")
    if kind == "stuck.raised":
        return "stuck"
    if kind == "proposal.raised":
        return _one_line(f"new task: {e['reason']}")
    if kind == "promotion.raised":
        return _one_line(f"promote: {d['action']} {d['key']}")
    if kind == "law.raised":
        return _one_line(f"law: {e['reason']}")
    return _one_line(e["reason"])


def _context(project: Project, e: dict, entries: list[dict], by_id: dict[str, dict], tasks: dict) -> dict:
    d, kind = e["data"], e["kind"]
    tid = d.get("task")
    t = tasks.get(tid, {})
    ctx: dict = {"goal": t.get("goal", "")}
    if kind == "decision.requested":
        ctx.update(action=d["action"], detail=e["reason"], key=d.get("key", ""),
                   waiting=t.get("status") in ("running", "launched"))
    elif kind == "disagreement.raised":
        verdict = by_id.get(d.get("verdict", ""), {})
        ctx.update(stage=d.get("stage"), why=e["reason"], verdict=verdict.get("data", {}).get("verdict", ""),
                   findings=verdict.get("data", {}).get("findings", []))
        finished = [x for x in entries if x["kind"] == "maker.finished" and x["data"].get("task") == tid]
        ctx["maker_summary"] = finished[-1]["reason"] if finished else ""
        if d.get("stage") == "plan":
            plans = [x for x in entries if x["kind"] == "plan.recorded" and x["data"].get("task") == tid]
            ctx["plan"] = plans[-1]["data"]["text"] if plans else ""
        else:
            ctx["diff"] = _diff(project, tid)
    elif kind == "stuck.raised":
        refused = [x for x in entries if x["kind"] in ("action.refused", "guard.tripped") and x["data"].get("task") == tid]
        ctx.update(why=e["reason"], refusals=[
            {"ts": x["ts"], "action": x["data"].get("action", ""), "detail": _one_line(x["data"].get("key") or x["reason"], 200),
             "why": x["data"].get("why", "")} for x in refused[-10:]])
    elif kind == "proposal.raised":
        ctx.update(goal=e["reason"], why=d.get("why", ""), profile=d["profile"], plan=d["plan"])
    elif kind == "promotion.raised":
        approvals = []
        for rid in d.get("evidence", []):
            r = by_id.get(rid)
            if r:
                approvals.append({"ts": r["ts"], "task": r["data"].get("task"), "reason": r["reason"]})
        ctx.update(action=d["action"], key=d["key"], profile=d["profile"], approvals=approvals)
    elif kind == "law.raised":
        cited = []
        for rid in d.get("evidence", []):
            r = by_id.get(rid)
            if not r:
                continue
            about = by_id.get(r["data"].get("decision"), {})
            ad = about.get("data", {})
            what = f"{ad['action']} {ad.get('key') or about.get('reason', '')}" if ad.get("action") else about.get("reason", "")
            cited.append({"id": rid, "ts": r["ts"], "reason": r["reason"], "about": _one_line(what, 200),
                          "kind": KIND_NAMES.get(about.get("kind", ""), about.get("kind", ""))})
        ctx.update(text=e["reason"], rule=d.get("rule"), evidence=cited)
    return ctx


def inbox_view(project: Project) -> dict:
    entries = project.ledger.entries()
    by_id = {x["id"]: x for x in entries}
    tasks = project.tasks()
    recs = inbox.recommendations(project)
    groups = []
    for title, items in inbox.batched(project):
        out = []
        for e in items:
            rec = recs.get(e["id"])
            out.append({
                "id": e["id"], "kind": e["kind"], "kind_name": KIND_NAMES.get(e["kind"], e["kind"]),
                "ts": e["ts"], "task": e["data"].get("task"), "headline": headline(e),
                "choices": list(CHOICES.get(e["kind"], ("Approve", "Reject"))),
                "recommendation": {"option": rec["data"]["option"], "why": rec["reason"]} if rec else None,
                "context": _context(project, e, entries, by_id, tasks),
            })
        groups.append({"title": title, "items": out})
    return {"groups": groups, "count": sum(len(g["items"]) for g in groups)}


def tasks_view(project: Project) -> list[dict]:
    rows = [{"id": tid, "goal": t["goal"], "status": t["status"], "cost_usd": t.get("cost_usd"),
             "last": t.get("last"), "profile": t["profile"], "branch": t.get("branch")}
            for tid, t in project.tasks().items()]
    return list(reversed(rows))  # newest first


def _event_text(e: dict) -> str | None:
    d, k = e["data"], e["kind"]
    target = _one_line(d.get("key") or e["reason"], 160)
    texts = {
        "task.created": "created", "task.queued": "queued for the next pulse", "run.launched": "launched by pulse",
        "maker.started": f"maker started ({d.get('stage')})",
        "maker.finished": f"maker finished ({d.get('stage')}): {d.get('status')}",
        "action.granted": f"did {d.get('action')} {target}",
        "action.refused": f"refused {d.get('action')} {target}: {d.get('why', '')}",
        "guard.tripped": f"guard refused {d.get('action')}: {d.get('why', '')}",
        "decision.requested": f"asked you: {d.get('action')} {target}",
        "decision.resolved": f"you {d.get('outcome')}: {e['reason']}",
        "plan.recorded": "plan written",
        "verdict.recorded": f"checker ({d.get('stage')}): {d.get('verdict')}",
        "disagreement.raised": f"disagreement: {_one_line(e['reason'])}",
        "stuck.raised": f"stuck: {_one_line(e['reason'])}",
        "report.recorded": "report written",
    }
    return texts.get(k)


def task_view(project: Project, task_id: str) -> dict:
    t = project.task(task_id)
    timeline = []
    for e in project.ledger.entries():
        if e["data"].get("task") == task_id:
            text = _event_text(e)
            if text:
                timeline.append({"ts": e["ts"], "kind": e["kind"], "text": text})
    reports = [x for x in project.ledger.entries() if x["kind"] == "report.recorded" and x["data"].get("task") == task_id]
    return {
        "id": task_id, "goal": t["goal"], "status": t["status"], "cost_usd": t.get("cost_usd"),
        "profile": t["profile"], "branch": t.get("branch"), "timeline": timeline[-200:],
        "diff": _diff(project, task_id), "report": reports[-1]["data"]["text"] if reports else "",
        "merge": f"git merge {t['branch']}" if t["status"] == "ready" and t.get("branch") else "",
    }
