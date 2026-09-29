"""What the UI shows, built from the ledger. Plain data, no HTTP, so it's testable.

The board is every task by state. A task's card is exactly `parallax show`, parsed into its
sections, so the terminal and the browser can never disagree about a task.
"""
from __future__ import annotations

import re

from . import decide, inbox, lifecycle, show, status
from .core import Project

MAX_TEXT = 200_000
SECTIONS = ("Decisions", "Changed since last time", "Found", "Recommended", "Details")


def board(project: Project) -> dict:
    """{columns: {state: [tasks, newest first]}, waiting: how many wait on you}."""
    columns: dict[str, list[dict]] = {state: [] for state in status.BOARD}
    for tid, t in project.tasks().items():
        if not t.get("intent"):
            continue
        state = status.board(t["status"])
        columns[state].append({"task": tid, "title": inbox.title(project, tid), "status": t["status"],
                               "cost_usd": round(t.get("cost_usd") or 0, 2), "last": t.get("last")})
    for tasks in columns.values():
        tasks.reverse()
    return {"columns": columns, "waiting": len(columns["ready"]) + len(columns["needs you"])}


def parse_report(text: str) -> dict:
    """The output shape, as data: the four header fields and each section's items."""
    out: dict = {"type": "", "bottom": "", "not_looked_at": "", "next": "", "sections": {}}
    keys = {"Type": "type", "Bottom line": "bottom", "Not looked at": "not_looked_at", "Next": "next"}
    current = None
    for line in text.splitlines():
        m = re.match(r"^(Type|Bottom line|Not looked at|Next):\s*(.*)$", line)
        if m and current is None:
            out[keys[m.group(1)]] = m.group(2)
        elif line.strip().lstrip("# ").strip() in SECTIONS:
            current = line.strip().lstrip("# ").strip()
            out["sections"][current] = []
        elif current and line.startswith("- "):
            out["sections"][current].append(line[2:])
    return out


def card(project: Project, task_id: str) -> dict:
    """Everything the decision card needs: the report, and what you can do from it."""
    t = project.task(task_id)
    report = parse_report(show.report(project, task_id))
    dec = decide.decision(project, task_id)
    if dec is not None:
        actions = {"kind": "decide", "question": dec.question, "recommend": dec.recommend,
                   "options": [{"name": o.name, "does": o.does, "needs_reason": o.needs_reason} for o in dec.options]}
    elif t["status"] == "ready":
        actions = {"kind": "ready"}
    else:
        actions = {"kind": "none"}
    merge = ""
    if t["status"] == "accepted":
        from .accept import merge_command
        accepted = [e for e in project.ledger.entries() if e["kind"] == "task.accepted" and e["data"]["task"] == task_id]
        merge = merge_command(accepted[-1]) if accepted else ""
    return {"task": task_id, "title": inbox.title(project, task_id), "status": t["status"],
            "state": status.board(t["status"]), "cost_usd": round(t.get("cost_usd") or 0, 2),
            "report": report, "actions": actions, "merge": merge,
            "docs": [d for d in ("intent", "spec", "plan", "record") if lifecycle.doc_path(project, task_id, d).exists()]}


def document(project: Project, task_id: str, name: str) -> str:
    """The diff, or one of the task's documents, as plain text."""
    if name == "diff":
        from .cli import _reviewed_diff
        text = _reviewed_diff(project, task_id)
    elif name in ("intent", "spec", "plan", "record"):
        path = lifecycle.doc_path(project, task_id, name)
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    else:
        raise ValueError(f"no document {name!r}")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n\n(cut here. the rest: parallax diff {task_id})"
    return text
