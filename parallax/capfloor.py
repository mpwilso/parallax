"""A floor for the plan's cap, from what Maker has really spent.

Focus estimates the work, and on unfamiliar code it guesses low: in eval run 41250f four plans
capped at $0.67 to $0.80 where Maker needed $1.22 to $2.00, so each stopped mid-build. The floor
comes from this repo's ledger once it has enough history (Maker's spend per task at the 90th
percentile, over past tasks of the same size); until then, from the policy's default for that
size. It's applied before approval (pilot.py), so the launch rule judges the raised cap as usual.
"""
from __future__ import annotations

import math

from . import lifecycle, lint
from .core import Project

MIN_HISTORY = 5   # tasks of a size before this repo's own record is used
PERCENTILE = 0.9


def spend_by_task(project: Project, size: str, exclude: str = "") -> list[float]:
    """Maker's recorded spend per past task of this size, every build and rework counted. A task
    with no intent (from before intents existed) has no size, so it isn't counted."""
    per_task: dict[str, float] = {}
    for e in project.ledger.entries():
        tid = e["data"].get("task")
        if e["kind"] == "maker.finished" and e["data"].get("cost_usd") and tid != exclude:
            per_task[tid] = per_task.get(tid, 0.0) + float(e["data"]["cost_usd"])
    sizes = {tid: lint.intent_fields(lifecycle._read(project, tid, "intent")).get("size") for tid in per_task}
    return [cost for tid, cost in per_task.items() if sizes[tid] == size]


def percentile(values: list[float], p: float) -> float:
    """Nearest rank: the smallest value with at least p of them at or below it."""
    ordered = sorted(values)
    return ordered[max(math.ceil(p * len(ordered)) - 1, 0)]


def floor(project: Project, size: str, exclude: str = "") -> tuple[float, str, str]:
    """(Maker's floor in estimated dollars, where it came from, "ledger" or "policy")."""
    spends = spend_by_task(project, size, exclude)
    if len(spends) >= MIN_HISTORY:
        return (round(percentile(spends, PERCENTILE), 2),
                f"Maker's 90th-percentile spend over this repo's {len(spends)} {size} tasks", "ledger")
    key = f"{size}_floor_usd"
    return (float(project.policy.budget[key]),
            f"the policy's {key} (this repo has {len(spends)} {size} tasks with Maker costs; "
            f"its own record counts from {MIN_HISTORY})", "policy")
