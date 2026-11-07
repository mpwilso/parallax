"""`parallax stats`: how much of you each task took, from the ledger.

A human touch is anything you had to do for a task besides describing it and merging it:
every approve, reject, decision, accept, cap raise or stop you made, every command you ran to
fix it up, and every drafted file you edited by hand (its hash changed between the draft and its
approval). The target is one touch per task: the final accept or reject.
A request Focus split into parts (conductor.py) gets a line too: its parts' touches and cost, summed.
"""
from __future__ import annotations

from . import status
from .core import Project

TOUCH_KINDS = {"gate.approved", "gate.rejected", "task.rejected", "task.accepted", "decision.resolved",
               "risk.accepted", "task.stopped", "human.command", "budget.raised", "task.redraft"}
TARGET = 1


def touches(project: Project) -> dict[str, int]:
    """Human touches per lifecycle task, oldest first."""
    entries = project.ledger.entries()
    out: dict[str, int] = {tid: 0 for tid, t in project.tasks().items() if t.get("intent")}
    drafted: dict[tuple[str, str], str] = {}
    for e in entries:
        tid, d = e["data"].get("task"), e["data"]
        if tid not in out:
            continue
        if e["kind"] == "draft.recorded":
            drafted[(tid, d["doc"])] = d.get("sha", "")
        if e["kind"] in TOUCH_KINDS and e["actor"] == "human" and not (e["kind"] == "gate.approved" and d.get("answer")):
            # an approval carrying out your "allow and launch" is the same touch as that answer, not a second one
            out[tid] += 1
        if e["kind"] == "gate.approved":  # a hand edit shows as a hash the drafter never wrote
            out[tid] += sum(1 for doc, sha in d["files"].items() if drafted.get((tid, doc), sha) != sha)
    return out


def partial(project: Project) -> dict[str, list[dict]]:
    """Per task, the sessions that ended early: their costs are partial, or unknown."""
    out: dict[str, list[dict]] = {}
    for e in project.ledger.entries():
        if e["kind"] == "agent.ended_early":
            out.setdefault(e["data"].get("task"), []).append(e)
    return out


def per_request(project: Project, counts: dict[str, int], tasks: dict[str, dict]) -> list[str]:
    """A request split into parts: its touches and cost, the sum of its parts'. Nothing when none was split."""
    from .conductor import groups
    split = groups(project)
    if not split:
        return []
    out = [f"{'request':<8}{'parts':>8}{'touches':>8}{'cost':>9}"]
    for g in split:
        made = [p["task"] for p in g["parts"] if p["task"] in tasks]
        cost = sum(tasks[t].get("cost_usd") or 0 for t in made)
        out.append(f"{g['request']:<8}{len(g['parts']):>8}{sum(counts.get(t, 0) for t in made):>8}{'$' + format(cost, '.2f'):>9}")
    return out


def report(project: Project) -> list[str]:
    from .evals import staleness
    evals = staleness(project)
    return _tasks(project) + ([evals] if evals else [])


def _tasks(project: Project) -> list[str]:
    counts = touches(project)
    if not counts:
        return ["no tasks yet."]
    tasks = project.tasks()
    early = partial(project)
    lines = [f"{'task':<8}{'touches':>8}{'cost':>9}  status"]
    for tid, n in counts.items():
        mark = f"  ({len(early[tid])} partial)" if early.get(tid) else ""
        lines.append(f"{tid:<8}{n:>8}{'$' + format(tasks[tid].get('cost_usd') or 0, '.2f'):>9}  {tasks[tid]['status']}{mark}")
    lines += per_request(project, counts, tasks)
    ended =[e for es in early.values() for e in es]
    if ended:
        unknown = sum(1 for e in ended if e["data"].get("source") == "unknown")
        lines.append(f"{len(ended)} agent sessions ended early. their costs are counted as partial"
                     + (f"; {unknown} unknown, counted as $0." if unknown else "."))
    done = {tid: n for tid, n in counts.items() if status.board(tasks[tid]["status"]) == "done"}
    if not done:  # an unfinished task's count isn't final, so it stays out of the average
        lines.append(f"no finished tasks yet. target {TARGET} touch per task.")
        return lines
    average = sum(done.values()) / len(done)
    lines.append(f"average {average:.1f} touches per finished task ({len(done)} finished), target {TARGET}. "
                 f"{sum(n > TARGET for n in done.values())} took more.")
    return lines

