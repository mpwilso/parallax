"""The inbox, batched for a human: items grouped by task."""
from __future__ import annotations

from .core import Project


def batched(project: Project) -> list[tuple[str, list[dict]]]:
    """Inbox items grouped, one group per task."""
    tasks = project.tasks()
    groups: dict[str, list[dict]] = {}
    for e in project.inbox():
        tid = e["data"].get("task")
        if tid in tasks:
            t = tasks[tid]
            header = f"task {tid}  [{t['status']}]  {t['goal']}"
        else:
            header = "other"
        groups.setdefault(header, []).append(e)
    return list(groups.items())


def label(e: dict) -> str:
    d = e["data"]
    if e["kind"] == "disagreement.raised":
        return f"disagreement ({d['stage']})"
    if e["kind"] == "stuck.raised":
        return "stuck"
    return d["action"]
