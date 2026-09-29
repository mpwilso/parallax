"""The permission function every agent calls before acting. This is the tool layer.

Order: guard (protected paths, reads outside the allowed roots) -> the scope -> record.
- With a Scope (a build from an approved plan), routine work inside the worktree is allowed, and
  anything that crosses the boundary must be in the plan: reads outside the worktree only in the
  plan's roots, network only to the plan's domains, any other tool refused.
- Without one, only a read-only stage (the drafters) runs: reads inside the allowed roots are
  granted; anything else is refused.
Repeated refusals of the same call mean the agent is stuck: it's stopped and you're told.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from . import guard
from .agents.base import Permission, PermissionFn
from .core import Project

ROUTINE = ("fs.read", "fs.write", "shell.run")


@dataclass(frozen=True)
class Scope:
    """What an approved plan lets cross the boundary."""
    reads: tuple[Path, ...] = ()   # read roots besides the worktree: the task's venv, the plan's reads
    domains: tuple[str, ...] = ()  # network domains; empty means none


def host_allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = (urlparse(url if "//" in url else f"//{url}").hostname or "").lower()
    for d in domains:
        d = d.lower()
        if host == d or (d.startswith("*.") and host.endswith(d[1:])):
            return True
    return False


def make_permission_fn(
    project: Project,
    task_id: str,
    worktree: Path,
    *,
    read_only: bool = False,
    scope: Scope | None = None,
) -> PermissionFn:
    snapshot = guard.fingerprint(project.root)
    stuck_after = project.policy.limits["stuck_after"]
    refusals: Counter = Counter()
    stopped: str | None = None

    def refuse(action: str, detail: str, why: str) -> Permission:
        project.ledger.append("guard.tripped", "parallax", detail, task=task_id, action=action, why=why)
        return Permission(False, f"refused: {why}")

    def decide(action: str, detail: str, paths: list[str] | None) -> Permission:
        nonlocal stopped
        if guard.fingerprint(project.root) != snapshot:
            stopped = "protected files changed during the run, all actions stopped"
            return refuse(action, detail, stopped)

        if read_only and action != "fs.read":
            project.ledger.append("action.refused", "agent", detail, task=task_id, action=action,
                                  why="read-only stage")
            return Permission(False, "refused: this stage is read-only")
        if action == "fs.read" and (read_only or scope):
            for target in paths or [detail]:
                why = guard.check_read(target, worktree, scope.reads if scope else ())
                if why:
                    return refuse(action, target, why)
        elif action == "fs.write":
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

        if scope is not None:
            return _rule_by_scope(project, task_id, scope, action, detail)
        if read_only and action == "fs.read":  # passed the read check above
            project.ledger.append("action.granted", "agent", detail, task=task_id, action=action, key=detail)
            return Permission(True)
        why = "no approved plan: only a read-only stage runs without one"
        project.ledger.append("action.refused", "agent", detail, task=task_id, action=action, key=detail, why=why)
        return Permission(False, f"refused: {why}")

    def permission_fn(action: str, detail: str = "", paths: list[str] | None = None) -> Permission:
        nonlocal stopped
        if stopped:
            return Permission(False, f"stopped: {stopped}", stop=True)
        p = decide(action, detail, paths)
        if stopped:
            return Permission(False, p.message, stop=True)
        if p.allowed:
            return p
        refusals[(action, detail)] += 1
        if refusals[(action, detail)] >= stuck_after:
            stopped = f"the same call was refused {stuck_after} times: {action} {detail}".rstrip()
            project.ledger.append("stuck.raised", "parallax", stopped, task=task_id, action=action)
            return Permission(False, f"stopped: {stopped}", stop=True)
        return p

    return permission_fn


def _rule_by_scope(project: Project, task_id: str, scope: Scope, action: str, detail: str) -> Permission:
    if action in ROUTINE or (action == "net.fetch" and host_allowed(detail, scope.domains)):
        project.ledger.append("action.granted", "agent", detail, task=task_id, action=action, key=detail)
        return Permission(True)
    why = ("not in the plan's network domains" if action == "net.fetch"
           else "not in the approved plan, so it's refused")
    project.ledger.append("action.refused", "agent", detail, task=task_id, action=action, key=detail, why=why)
    return Permission(False, f"refused: {why}")

