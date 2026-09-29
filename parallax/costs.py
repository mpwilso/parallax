"""A task's budget: one cap, from the approved plan, over everything the task spends.

Every recorded cost counts, from the first drafting call on: drafting, build, check and rework.
Drafting runs before the cap exists, under its per-call ceiling, and still counts against it.
Each agent call gets what's left as its own ceiling, and Parallax checks the total before and
after every call. At the cap the task stops and comes to you as Decision needed. Every figure is
an estimate, at API list prices, as Claude Code computes it.
"""
from __future__ import annotations

from .core import Project


def spent(project: Project, task_id: str) -> float:
    return round(sum(e["data"].get("cost_usd") or 0 for e in project.ledger.entries()
                     if e["data"].get("task") == task_id), 4)


def budget(project: Project, task_id: str, plan: dict) -> tuple[float, float]:
    """(cap, left): the plan's cap and what's left of it."""
    cap = float(plan["budget_cap_usd"])
    return cap, round(cap - spent(project, task_id), 4)


def stop_at_cap(project: Project, task_id: str, cap: float, doing: str = "") -> str:
    """The task stops here and comes to you. Returns the task's new status.

    doing: what the work was trying to do when the money ran out, such as a rework's fix."""
    why = f"the budget cap ran out (${spent(project, task_id):.2f} of ${cap:.2f} estimated)"
    why += f" while {doing}" if doing else ""
    project.ledger.append("stuck.raised", "parallax", why, task=task_id, budget=True)
    return "stuck"
