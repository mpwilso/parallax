"""The blind checker: sees the task goal and the material under review, nothing else.

Never the maker's summary, reasoning, or commit messages (invariant 3).
Anything short of agreement goes to the human inbox, never retried (invariant 4).
"""
from __future__ import annotations

import hashlib

from .agents.base import AGREE, VERDICTS, Checker, CheckerError
from .core import Project


def diff_material(project: Project, task_id: str) -> str:
    return project.diff(task_id, "--function-context")


def review(project: Project, task_id: str, checker: Checker, kind: str, material: str,
           **refs) -> tuple[dict, dict | None]:
    """Run the checker once. Returns (verdict entry, disagreement entry or None)."""
    goal = project.task(task_id)["goal"]
    digest = hashlib.sha256(material.encode()).hexdigest()
    cost = None
    try:
        v = checker.review(goal, material, kind)
        cost = v.cost_usd
        if v.verdict not in VERDICTS:
            raise CheckerError(f"unknown verdict {v.verdict!r}")
        verdict, findings, note = v.verdict, list(v.findings), "; ".join(v.findings)
    except CheckerError as err:
        verdict, findings, note = "error", [], str(err)

    ve = project.ledger.append("verdict.recorded", "checker", note, task=task_id, stage=kind,
                               verdict=verdict, findings=findings, material=digest, cost_usd=cost, **refs)
    if verdict in AGREE:
        return ve, None
    why = f"checker error: {note}" if verdict == "error" else f"maker says done, checker says fail: {note or 'no findings given'}"
    de = project.ledger.append("disagreement.raised", "parallax", why, task=task_id, stage=kind,
                               verdict=ve["id"], checker=verdict)
    return ve, de
