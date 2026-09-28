"""What the visual inbox shows, built from the ledger. Plain data, no HTTP, so it's testable.

Every item carries the context a human needs to decide it, and the plain-word labels for the
two choices, which depend on the kind of item.
"""
from __future__ import annotations

import subprocess

from . import inbox
from .core import Project

MAX_DIFF = 200_000

CHOICES = {
    "decision.requested": ("Allow", "Refuse"),
    "disagreement.raised": ("Side with the maker", "Side with the checker"),
    "stuck.raised": ("Let it run again", "Close the task"),
}
KIND_NAMES = {"decision.requested": "permission", "disagreement.raised": "disagreement", "stuck.raised": "stuck"}
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
    return _one_line(e["reason"])


def _context(project: Project, e: dict, entries: list[dict], by_id: dict[str, dict], tasks: dict) -> dict:
    d, kind = e["data"], e["kind"]
    tid = d.get("task")
    t = tasks.get(tid, {})
    ctx: dict = {"goal": t.get("goal", "")}
    if kind == "decision.requested":
        ctx.update(action=d["action"], detail=e["reason"], key=d.get("key", ""),
                   waiting=t.get("status") == "running")
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
    return ctx


def inbox_view(project: Project) -> dict:
    entries = project.ledger.entries()
    by_id = {x["id"]: x for x in entries}
    tasks = project.tasks()
    groups = []
    for title, items in inbox.batched(project):
        out = []
        for e in items:
            out.append({
                "id": e["id"], "kind": e["kind"], "kind_name": KIND_NAMES.get(e["kind"], e["kind"]),
                "ts": e["ts"], "task": e["data"].get("task"), "headline": headline(e),
                "choices": list(CHOICES.get(e["kind"], ("Approve", "Reject"))),
                "context": _context(project, e, entries, by_id, tasks),
            })
        groups.append({"title": title, "items": out})
    return {"groups": groups, "count": sum(len(g["items"]) for g in groups)}


def tasks_view(project: Project) -> list[dict]:
    rows = [{"id": tid, "goal": t["goal"], "status": t["status"], "cost_usd": t.get("cost_usd"),
             "last": t.get("last"), "branch": t.get("branch")}
            for tid, t in project.tasks().items()]
    return list(reversed(rows))  # newest first


def _event_text(e: dict) -> str | None:
    d, k = e["data"], e["kind"]
    target = _one_line(d.get("key") or e["reason"], 160)
    texts = {
        "task.created": "created",
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
    return {
        "id": task_id, "goal": t["goal"], "status": t["status"], "cost_usd": t.get("cost_usd"),
        "branch": t.get("branch"), "timeline": timeline[-200:], "diff": _diff(project, task_id),
        "merge": merge_steps(t) if t["status"] == "ready" and t.get("branch") else "",
    }


def merge_steps(t: dict) -> str:
    """Exactly what to type to merge a ready task. The maker's work may not be committed yet."""
    wt = t["worktree"]
    try:
        dirty = subprocess.run(["git", "-C", wt, "status", "--porcelain"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace").stdout.strip()
    except OSError:
        dirty = ""
    steps = []
    if dirty:
        message = _one_line(t["goal"].splitlines()[0], 72).replace('"', "'")
        steps += [f'git -C "{wt}" add -A', f'git -C "{wt}" commit -m "{message}"']
    steps.append(f"git merge {t['branch']}")
    return "\n".join(steps)
