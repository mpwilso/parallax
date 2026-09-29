"""What a working task is doing right now, in one line, from the ledger.

Which agent has it (the drafters, the maker, the checker, or Parallax itself running tests),
for how long, and what this attempt has spent against its cap. For the UI's working list.
"""
from __future__ import annotations

from datetime import datetime, timezone

from . import costs, lifecycle, lint, status
from .core import Project

REWORK_CAP = 3


def _since(ts: str, now: datetime) -> str:
    try:
        then = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    secs = max(0, int((now - then).total_seconds()))
    return f"{secs}s" if secs < 60 else f"{secs // 60}m" if secs < 3600 else f"{secs // 3600}h {secs % 3600 // 60}m"


def doing(entries: list[dict]) -> tuple[str, str, str]:
    """(what, why, since ts) for one attempt's entries, oldest first. why: a rework's first finding."""
    what, why, since = "starting", "", entries[0]["ts"] if entries else ""
    drafted: set[str] = set()
    for e in entries:
        k, d = e["kind"], e["data"]
        if k in ("pilot.started", "task.redraft", "check.started"):
            why = ""  # a new stage: the rework's finding no longer describes it
        if k in ("pilot.started", "task.redraft", "task.created"):
            what, since, drafted = "drafters writing the intent", e["ts"], set()
        elif k == "draft.recorded":
            drafted.add(d.get("doc", ""))
            what = "drafters writing the plan" if "plan" not in drafted else "checking the plan against the intent"
            since = e["ts"]
        elif k in ("draft.misfit", "draft.failed"):
            what, since = "drafters redrafting", e["ts"]
        elif k == "gate.approved":
            what, since = "preparing the build", e["ts"]
        elif k == "maker.started":
            what, since = "maker building", e["ts"]
        elif k == "rework.started":
            why = lint.one_sentence(e["reason"].splitlines()[0] if e["reason"] else "").rstrip(".")
            what, since = f"maker reworking ({d.get('cycle', 1)} of {REWORK_CAP})", e["ts"]
        elif k == "check.started":
            what, since = "running the plan's tests", e["ts"]
        elif k == "tests.recorded":
            what, since = "checker reviewing the change", e["ts"]
    return what, why, since


def line(project: Project, task_id: str, now: datetime | None = None) -> str:
    """e.g. "maker building, 2m, $0.40 of $2.00". A rework adds what it's fixing, last."""
    now = now or datetime.now(timezone.utc)
    what, why, since = doing(status.attempt(project.ledger.entries(), task_id))
    parts = [what]
    if since and (took := _since(since, now)):
        parts.append(took)
    plan = lifecycle.plan_data(project, task_id)
    spent = costs.spent(project, task_id)
    if plan:
        parts.append(f"${spent:.2f} of ${costs.budget(project, task_id, plan)[0]:.2f}")
    elif spent:
        parts.append(f"${spent:.2f} spent")
    return ", ".join(parts) + (f". {why[0].upper()}{why[1:]}" if why else "")
