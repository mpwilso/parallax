"""Run one task: optional plan and plan check, then build and the blind diff check."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import guard
from .agents.base import AGREE, Agent, AgentResult, Checker
from .checker import diff_material, review
from .core import ParallaxError, Project
from .gate import make_permission_fn


def _quiet(msg: str) -> None:
    pass


def approved_plan(project: Project, task_id: str) -> str | None:
    """The latest plan, if the plan checker agreed or a human sided with the maker."""
    text, ok, disputes = None, False, {}
    for e in project.ledger.entries():
        kind, d = e["kind"], e["data"]
        if d.get("task") != task_id:
            continue
        if kind == "plan.recorded":
            text, ok = d["text"], False
        elif kind == "verdict.recorded" and d["stage"] == "plan":
            ok = d["verdict"] in AGREE
        elif kind == "disagreement.raised" and d["stage"] == "plan":
            disputes[e["id"]] = True
        elif kind == "decision.resolved" and d.get("decision") in disputes:
            ok = d["outcome"] == "approved"
    return text if ok else None


def ensure_not_disputed(project: Project, task_id: str) -> dict:
    t = project.task(task_id)
    if t["status"] == "disputed":
        raise ParallaxError(f"task {task_id} has an open disagreement. resolve it in the inbox first")
    return t


def _make(project: Project, task_id: str, maker: Agent, goal: str, stage: str, fn) -> AgentResult:
    project.ledger.append("maker.started", "parallax", "", task=task_id, stage=stage)
    try:
        res = maker.run(goal, Path(project.task(task_id)["worktree"]), fn, stage=stage)
    except Exception as err:  # an adapter crash is recorded, not hidden
        res = AgentResult("error", f"{type(err).__name__}: {err}")
    summary = "" if stage == "plan" else res.summary  # the plan text goes in plan.recorded
    project.ledger.append("maker.finished", "maker", summary, task=task_id, stage=stage, status=res.status)
    return res


def run_task(project: Project, task_id: str, maker: Agent, checker: Checker, *, plan: bool = False,
             poll: float = 1.0, on_wait: Callable[[dict], None] | None = None,
             say: Callable[[str], None] = _quiet) -> str:
    t = ensure_not_disputed(project, task_id)
    wt, goal = Path(t["worktree"]), t["goal"]

    if plan:
        say("maker planning (read-only)")
        fn = make_permission_fn(project, task_id, wt, read_only=True, poll=poll, on_wait=on_wait)
        res = _make(project, task_id, maker, goal, "plan", fn)
        if res.status != "done":
            return project.task(task_id)["status"]
        p = project.ledger.append("plan.recorded", "maker", "", task=task_id, text=res.summary)
        say("checker reviewing plan")
        ve, dis = review(project, task_id, checker, "plan", res.summary, plan=p["id"])
        say(f"checker: {ve['data']['verdict']}")
        if dis:
            say(f"disagreement {dis['id']} is in your inbox")
            return project.task(task_id)["status"]

    build_goal = goal
    agreed = approved_plan(project, task_id)
    if agreed:
        build_goal = f"{goal}\n\nFollow this approved plan:\n{agreed}"

    say("maker building")
    before = guard.fingerprint(project.root)
    fn = make_permission_fn(project, task_id, wt, poll=poll, on_wait=on_wait)
    res = _make(project, task_id, maker, build_goal, "build", fn)
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

    if res.status != "done":
        return project.task(task_id)["status"]

    say("checker reviewing diff")
    ve, dis = review(project, task_id, checker, "diff", diff_material(project, task_id))
    say(f"checker: {ve['data']['verdict']}")
    if dis:
        say(f"disagreement {dis['id']} is in your inbox")
    return project.task(task_id)["status"]
