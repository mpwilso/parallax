"""Stop one task at any stage, or every running one: `parallax stop [task]`, or Stop on its card.

What runs for a task: its pilot or builder process, in a session of its own (drafting, Reticle,
Maker, the checks and Field all run inside it, agents included), or, during Accept and merge, the
pre-merge test run, which `parallax ui` starts and whose pid is kept in the task's data folder.
Stopping ends that within a few seconds (TERM, then KILL after GRACE), and records it as your
action, with what it was doing and what it had spent. Nothing else is touched: a stop during the
pre-merge test run never moves the base branch (accept.py checks before every step that could).

A stopped task is Needs you: resume it from where it stopped (finished stages aren't redone), send
it back with a reason, or drop it. Its work so far stays in its worktree.
"""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path

from . import costs, live, memcap, sandbox, status
from .core import ParallaxError, Project, refuse_inside_task

GRACE = 3.0  # seconds between asking a process to end and making it


def merge_pidfile(project: Project, task_id: str) -> Path:
    """Where the pre-merge test run's pid is kept while it runs."""
    return sandbox.task_home(project.root, task_id) / "merge-tests.pid"


def _alive(pid: int) -> bool:
    from .build import _alive as running_now  # a zombie has ended
    return running_now(pid)


def running(project: Project) -> dict[str, dict]:
    """task -> {builder, tests}: what can be stopped. A merging task counts even between its steps."""
    from .build import running_builds
    out: dict[str, dict] = {tid: {"builder": pid, "tests": None} for tid, pid in running_builds(project).items()}
    for tid, t in project.tasks().items():
        if t["status"] == "merging":
            pid = _pid(merge_pidfile(project, tid))
            out.setdefault(tid, {"builder": None, "tests": None})["tests"] = pid
    return out


def _pid(path: Path) -> int | None:
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    return pid if _alive(pid) else None


def doing(project: Project, task_id: str) -> str:
    """What the task was doing, in a few words, for the ledger and its card."""
    if project.task(task_id)["status"] == "merging":
        return "the pre-merge test run"
    return live.doing(status.attempt(project.ledger.entries(), task_id))[0]


def _end_session(pid: int, grace: float) -> None:
    """A builder: its whole session, every agent and command in it."""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return
        deadline = time.monotonic() + grace
        while _alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        if not _alive(pid):
            return


def stop(project: Project, task_id: str | None = None, reason: str = "parallax stop", grace: float = GRACE) -> list[str]:
    """End one task's processes now, or every running task's. Recorded as yours. Returns the tasks stopped."""
    refuse_inside_task(project.root)
    targets = running(project)
    if task_id is not None:
        project.task(task_id)  # an unknown id is an error, not "nothing to stop"
        if task_id not in targets:
            raise ParallaxError(f"task {task_id} isn't running, so there's nothing to stop. parallax show {task_id} says where it is")
        targets = {task_id: targets[task_id]}
    stopped = []
    for tid, procs in targets.items():
        what = doing(project, tid)
        if procs["tests"]:  # started by parallax ui, in its own process group: only that tree, never the server
            memcap.kill_tree(procs["tests"])
        if procs["builder"] and _alive(procs["builder"]):
            _end_session(procs["builder"], grace)
        project.ledger.append("task.stopped", "human", reason, task=tid, pid=procs["builder"] or procs["tests"],
                              stage=what, spent_usd=round(costs.spent(project, tid), 4))
        stopped.append(tid)
    return stopped


def stopped_since(project: Project, task_id: str, kind: str) -> bool:
    """You stopped this task after its last entry of kind (merge.started, say)."""
    for e in reversed([e for e in project.ledger.entries() if e["data"].get("task") == task_id]):
        if e["kind"] == "task.stopped":
            return True
        if e["kind"] == kind:
            return False
    return False


def last_stop(project: Project, task_id: str) -> dict | None:
    stops = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] == "task.stopped"]
    return stops[-1] if stops else None
