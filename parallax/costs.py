"""A task's budget: one cap, from the approved plan, over everything the task spends.

Every recorded cost counts, from the first drafting call on: drafting, build, check and rework.
Drafting runs before the cap exists, under its per-call ceiling, and still counts against it.
Each agent call gets what's left as its own ceiling, and Parallax checks the total before and
after every call. At the cap the task stops and comes to you as Decision needed. Every figure is
an estimate, at API list prices, as Claude Code computes it.
"""
from __future__ import annotations

from . import status
from .core import Project


def spent(project: Project, task_id: str) -> float:
    """What this attempt has spent. After a reject at Ready, the redrafted plan's cap starts fresh."""
    return round(sum(e["data"].get("cost_usd") or 0 for e in status.attempt(project.ledger.entries(), task_id)), 4)


def raised(project: Project, task_id: str) -> float:
    """What you've added to this attempt's cap."""
    return round(sum(e["data"]["amount_usd"] for e in status.attempt(project.ledger.entries(), task_id)
                     if e["kind"] == "budget.raised"), 4)


def budget(project: Project, task_id: str, plan: dict) -> tuple[float, float]:
    """(cap, left): the plan's cap, plus anything you raised it by, and what's left of it."""
    from .budgets import effective_cap  # the mode, or a budget you named
    cap = round(effective_cap(project, task_id, float(plan["budget_cap_usd"])) + raised(project, task_id), 4)
    return cap, round(cap - spent(project, task_id), 4)


STAGES = {"drafter": "drafting", "reticle": "Reticle", "maker": "Maker", "checker": "Second Eye", "ui tester": "Field"}


def by_stage(project: Project, task_id: str) -> list[tuple[str, float]]:
    """What each stage of this attempt spent, largest first: (stage, dollars). From each entry's own
    cost and who wrote it; the Ask box's spend is its own budget and isn't here."""
    out: dict[str, float] = {}
    for e in status.attempt(project.ledger.entries(), task_id):
        if e["data"].get("cost_usd"):
            stage = STAGES.get(e["actor"], "other")
            out[stage] = out.get(stage, 0.0) + e["data"]["cost_usd"]
    return sorted(((s, round(v, 4)) for s, v in out.items()), key=lambda sv: -sv[1])


def last_stage(project: Project, task_id: str) -> str | None:
    """The stage that spent last: the one running when the money ran out."""
    hits = [e for e in status.attempt(project.ledger.entries(), task_id) if e["data"].get("cost_usd")]
    return STAGES.get(hits[-1]["actor"], "other") if hits else None


def spent_line(project: Project, task_id: str, cap: float) -> tuple[str, str]:
    """(who, all): who spent the cap, said so it never blames the stage that happened to run last,
    "Maker spent $3.10 of the $3.80 cap, leaving Field $0.20", and every stage's share, largest first."""
    stages, total = by_stage(project, task_id), spent(project, task_id)
    if not stages:
        return f"${total:.2f} of the ${cap:.2f} cap is spent", ""
    (top, most), last = stages[0], last_stage(project, task_id)
    who = f"{top} spent ${most:.2f} of the ${cap:.2f} cap"
    if last and last != top:
        who += f", leaving {last} ${max(cap - (total - dict(stages).get(last, 0.0)), 0):.2f}"
    rest = ", ".join(f"{s} ${v:.2f}" for s, v in stages) + f" (${total:.2f})" if len(stages) > 1 else ""
    return who, rest


def stop_at_cap(project: Project, task_id: str, cap: float, doing: str = "") -> str:
    """The task stops here and comes to you. Returns the task's new status.

    doing: what the work was trying to do when the money ran out, such as a rework's fix. The reason
    leads with who spent the cap: the stage that ran last often had only what the others left."""
    who, rest = spent_line(project, task_id, cap)
    why = f"{who}, so the budget cap ran out" + (f"; in all: {rest}" if rest else "") + (f"; it stopped while {doing}" if doing else "")
    project.ledger.append("stuck.raised", "parallax", why, task=task_id, budget=True)
    return "stuck"
