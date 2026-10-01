"""The inbox: exactly one item per task, derived from the ledger.

A task is in the inbox only when it's waiting on you: Ready (accept or reject it), or needs you
(one Decision needed). Everything else is working without you, or done.
"""
from __future__ import annotations

from . import lint, status
from .core import Project


def title(project: Project, task_id: str) -> str:
    from .lifecycle import _read
    t = project.task(task_id)
    name = lint.intent_fields(_read(project, task_id, "intent")).get("title") if t.get("intent") else ""
    return " ".join((name or t["goal"]).split())


def items(project: Project) -> list[dict]:
    """One per task waiting on you, oldest first: {task, state, title}. A task whose line can't be
    built is listed as that, with the error in the log, and the rest as usual."""
    out = []
    for tid, t in project.tasks().items():
        try:
            where = status.board(t["status"])
            if where in ("ready", "needs you"):
                from . import overlap
                said = [overlap.line(o) for o in overlap.of(project, tid)]
                out.append({"task": tid, "state": where, "title": title(project, tid), **({"overlaps": said} if said else {})})
        except Exception:
            from .views import log_broken
            log_broken(tid, "its inbox line")
            out.append({"task": tid, "state": "broken", "title": "couldn't display this task"})
    return out


def working(project: Project) -> int:
    return sum(status.board(t["status"]) in ("drafting", "building", "checking") for t in project.tasks().values())


def to_merge(project: Project) -> list[dict]:
    """Accepted tasks you haven't merged yet: merging is yours, so they're listed, not queued as items."""
    from .accept import merge_command
    accepted = {e["data"]["task"]: e for e in project.ledger.entries() if e["kind"] == "task.accepted"}
    return [{"task": tid, "command": merge_command(accepted[tid], project)}
            for tid, t in project.tasks().items() if t["status"] == "accepted" and tid in accepted]
