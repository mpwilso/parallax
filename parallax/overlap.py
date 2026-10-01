"""A heads-up when a new task's scope names a file an older task also changes.

The older task still waits on you, is Ready, or is accepted but not merged: two of them may land
the same change (real use: 370571 and 40171b both added the same hint, and both reached Ready). It
never blocks anything; the newer task's card and the inbox name the other task, and the files.
"""
from __future__ import annotations

from . import inbox, lifecycle, lint, planfit, status
from .core import Project

STILL_OPEN = ("needs you", "ready")  # on the board; accepted or merging tasks count too, until merged
UNMERGED = ("accepted", "merging")


def changes(project: Project, task_id: str) -> list[str]:
    """The files a task changes: what its check staged, or before that, its plan's files."""
    staged = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] == "check.staged"]
    if staged:
        return list(staged[-1]["data"].get("files") or [])
    plan = lifecycle.plan_data(project, task_id) or {}
    return [f.split("::", 1)[0] for f in plan.get("files") or []]


def _state(t: dict) -> str:
    return {"needs you": "waiting on you", "ready": "Ready"}.get(status.board(t["status"]), "accepted, not merged")


def of(project: Project, task_id: str) -> list[dict]:
    """[{task, title, state, files}]: older open tasks that change files this task's scope names."""
    tasks = project.tasks()
    me = tasks[task_id]
    if status.board(me["status"]) == "done" and me["status"] not in UNMERGED:
        return []
    scope = lint.scope_of(lifecycle._read(project, task_id, "intent")) if me.get("intent") else []
    if not scope:
        return []
    out = []
    for tid, t in tasks.items():
        if tid == task_id:
            break  # only older tasks: the newer one carries the heads-up
        if status.board(t["status"]) not in STILL_OPEN and t["status"] not in UNMERGED:
            continue
        files = [f for f in changes(project, tid) if planfit.in_scope(f, scope)]
        if files:
            out.append({"task": tid, "title": inbox.title(project, tid), "state": _state(t), "files": files})
    return out


def source(project: Project, task_id: str) -> str:
    """Where the other task's files come from, for a citation: its staged check, or its plan."""
    staged = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] == "check.staged"]
    return f"ledger {staged[-1]['id']}" if staged else f"docs/tasks/{task_id}/plan.md:1"


def cited(project: Project, task_id: str) -> list[str]:
    """This task's heads-ups as Found lines, for parallax show: the same words as the card and the inbox."""
    return [f"{line(o)} ({source(project, o['task'])})" for o in of(project, task_id)]


def line(o: dict) -> str:
    files = ", ".join(o["files"][:3]) + (f" and {len(o['files']) - 3} more" if len(o["files"]) > 3 else "")
    return f"Heads-up: task {o['task']} ({o['title']}), {o['state']}, also changes {files}."
