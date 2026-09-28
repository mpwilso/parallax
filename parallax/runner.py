"""`parallax run`: the old way, a task run straight from its goal, then the M2 diff check.

Lifecycle tasks go through build.py and check.py instead. This stays for `task new` and the evals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import guard
from .agents.base import Agent, AgentResult, Checker
from .checker import diff_material, review
from .core import ROOT_ENV, TASK_ENV, ParallaxError, Project
from .gate import make_permission_fn


def _quiet(msg: str) -> None:
    pass


def ensure_can_run(project: Project, task_id: str) -> dict:
    t = project.task(task_id)
    if t["status"] in ("disputed", "stuck"):
        raise ParallaxError(f"task {task_id} is {t['status']}. resolve it in the inbox first")
    if t["status"] == "closed":
        raise ParallaxError(f"task {task_id} is closed")
    return t


def _check_cap(project: Project, task_id: str) -> None:
    limit = project.policy.limits["max_parallel"]
    busy = [i for i, t in project.tasks().items() if t["status"] == "running" and i != task_id]
    if len(busy) >= limit:
        raise ParallaxError(f"{len(busy)} tasks already running (max_parallel = {limit})")


def _make(project: Project, task_id: str, maker: Agent, goal: str, stage: str, fn,
          extra_env: dict[str, str] | None = None) -> AgentResult:
    project.ledger.append("maker.started", "parallax", "", task=task_id, stage=stage)
    env = {**(extra_env or {}), TASK_ENV: task_id, ROOT_ENV: str(project.root)}
    try:
        res = maker.run(goal, Path(project.task(task_id)["worktree"]), fn, stage=stage, env=env)
    except Exception as err:  # an adapter crash is recorded, not hidden
        res = AgentResult("error", f"{type(err).__name__}: {err}")
    project.ledger.append("maker.finished", "maker", res.summary, task=task_id, stage=stage, status=res.status,
                          cost_usd=res.cost_usd)
    return res


def run_task(project: Project, task_id: str, maker: Agent, checker: Checker, *,
             say: Callable[[str], None] = _quiet, env: dict[str, str] | None = None) -> str:
    t = ensure_can_run(project, task_id)
    _check_cap(project, task_id)
    wt, goal = Path(t["worktree"]), t["goal"]

    say("maker building")
    before = guard.fingerprint(project.root)
    fn = make_permission_fn(project, task_id, wt)
    res = _make(project, task_id, maker, goal, "build", fn, extra_env=env)
    say(f"maker: {res.status}")

    # backstop for invariant 9: whatever got past the gate, the human hears about it
    problems = []
    touched = guard.protected_in(project.diff(task_id, "--name-only").splitlines())
    if touched:
        problems.append(f"diff touches protected files: {', '.join(touched)}")
    if guard.fingerprint(project.root) != before:
        problems.append("policy or mission file changed during the run")
    if problems:
        why = "; ".join(problems)
        project.ledger.append("guard.tripped", "parallax", why, task=task_id, action="fs.write", why=why)
        dis = project.ledger.append("disagreement.raised", "parallax", why, task=task_id, stage="guard")
        say(f"{why}. disagreement {dis['id']} is in your inbox")
        return project.task(task_id)["status"]

    if project.task(task_id)["status"] == "stuck":
        say("maker stuck. it's in your inbox")
        return "stuck"
    if res.status != "done":
        return project.task(task_id)["status"]

    say("checker reviewing diff")
    ve, dis = review(project, task_id, checker, "diff", diff_material(project, task_id))
    say(f"checker: {ve['data']['verdict']}")
    if dis:
        say(f"disagreement {dis['id']} is in your inbox")
    return project.task(task_id)["status"]


def flag_stale_runs(project: Project, now: datetime | None = None) -> list[str]:
    """A running task gone quiet, with nothing waiting on you, may have died. Flag it stuck.

    Runs on every command, in place of the old pulse.
    """
    from .build import _alive, running_builds

    now = now or datetime.now(timezone.utc)
    minutes = project.policy.limits["stale_minutes"]
    waiting_on_you = {e["data"].get("task") for e in project.inbox()}
    builds = running_builds(project)
    flagged = []
    for tid, t in project.tasks().items():
        if t["status"] != "running" or tid in waiting_on_you:
            continue
        if tid in builds and not _alive(builds[tid]):
            why = "the build process ended without finishing"
        elif datetime.fromisoformat(t["last"]) < now - timedelta(minutes=minutes):
            why = f"no activity for {minutes} minutes, the run may have died"
        else:
            continue
        project.ledger.append("stuck.raised", "parallax", why, task=tid)
        flagged.append(tid)
    return flagged
