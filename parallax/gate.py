"""The permission function every maker calls before acting.

Order: guard (invariant 9) -> policy -> wait for a human on `ask`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from . import guard
from .agents.base import Permission, PermissionFn
from .core import Project
from .policy import ALLOW, DENY


def make_permission_fn(
    project: Project,
    task_id: str,
    worktree: Path,
    *,
    read_only: bool = False,
    poll: float = 1.0,
    on_wait: Callable[[dict], None] | None = None,
) -> PermissionFn:
    snapshot = guard.fingerprint(project.root)
    tripped = False

    def refuse(action: str, detail: str, why: str) -> Permission:
        project.ledger.append("guard.tripped", "parallax", detail, task=task_id, action=action, why=why)
        return Permission(False, f"refused: {why}")

    def permission_fn(action: str, detail: str = "", paths: list[str] | None = None) -> Permission:
        nonlocal tripped
        if tripped or guard.fingerprint(project.root) != snapshot:
            tripped = True
            return refuse(action, detail, "protected files changed during the run, all actions stopped")

        if read_only and action != "fs.read":
            project.ledger.append("action.refused", "agent", detail, task=task_id, action=action,
                                  why="plan stage is read-only")
            return Permission(False, "refused: plan stage is read-only")

        if action == "fs.write":
            if not paths or not all(paths):
                return refuse(action, detail, "write with no path")
            for p in paths:
                why = guard.check_write(p, worktree)
                if why:
                    return refuse(action, p, why)
        elif action == "shell.run":
            why = guard.check_shell(detail, worktree)
            if why:
                return refuse(action, detail, why)

        res = project.check(task_id, action, detail)
        entry = res["entry"]
        if res["ruling"] == ALLOW:
            return Permission(True)
        if res["ruling"] == DENY:
            return Permission(False, f"refused: {entry['data']['why']}")

        if on_wait:
            on_wait(entry)
        while (outcome := _outcome(project, entry["id"])) is None:
            time.sleep(poll)
        if outcome["data"]["outcome"] == "approved":
            return Permission(True)
        return Permission(False, f"rejected by human: {outcome['reason']}")

    return permission_fn


def _outcome(project: Project, decision_id: str) -> dict | None:
    try:
        return project.decision_outcome(decision_id)
    except json.JSONDecodeError:
        return None  # another process is mid-append; read again next poll
