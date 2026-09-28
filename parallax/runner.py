"""Run one task: optional plan and plan check, then build and the blind diff check."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import guard, rules
from . import mission as missions
from .agents.base import AGREE, Agent, AgentResult, Checker
from .checker import diff_material, review
from .core import ROOT_ENV, TASK_ENV, ParallaxError, Project
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


def ensure_can_run(project: Project, task_id: str) -> dict:
    t = project.task(task_id)
    if t["status"] in ("disputed", "stuck"):
        raise ParallaxError(f"task {task_id} is {t['status']}. resolve it in the inbox first")
    if t["status"] == "closed":
        raise ParallaxError(f"task {task_id} is closed")
    return t


def _check_cap(project: Project, task_id: str) -> None:
    limit = project.policy.limits["max_parallel"]
    busy = [i for i, t in project.tasks().items() if t["status"] in ("running", "launched") and i != task_id]
    if len(busy) >= limit:
        raise ParallaxError(f"{len(busy)} tasks already running (max_parallel = {limit})")


def _make(project: Project, task_id: str, maker: Agent, goal: str, stage: str, fn,
          keep_summary: bool = True, extra_env: dict[str, str] | None = None) -> AgentResult:
    project.ledger.append("maker.started", "parallax", "", task=task_id, stage=stage)
    env = {**(extra_env or {}), TASK_ENV: task_id, ROOT_ENV: str(project.root)}
    try:
        res = maker.run(goal, Path(project.task(task_id)["worktree"]), fn, stage=stage, env=env)
    except Exception as err:  # an adapter crash is recorded, not hidden
        res = AgentResult("error", f"{type(err).__name__}: {err}")
    summary = res.summary if keep_summary else ""  # plans and reports get their own entries
    project.ledger.append("maker.finished", "maker", summary, task=task_id, stage=stage, status=res.status,
                          cost_usd=res.cost_usd)
    return res


def run_task(project: Project, task_id: str, maker: Agent, checker: Checker, *, plan: bool | None = None,
             poll: float = 1.0, on_wait: Callable[[dict], None] | None = None,
             say: Callable[[str], None] = _quiet, env: dict[str, str] | None = None) -> str:
    t = ensure_can_run(project, task_id)
    _check_cap(project, task_id)
    wt, goal = Path(t["worktree"]), t["goal"]
    readonly = t["profile"] == "readonly"
    m = missions.load(project.root)
    laws = f"\n\nLaws for working on this project:\n{m.how}" if m and m.how else ""
    if plan is None:
        plan = t["plan"]

    if plan:
        say("maker planning (read-only)")
        fn = make_permission_fn(project, task_id, wt, read_only=True, poll=poll, on_wait=on_wait)
        res = _make(project, task_id, maker, goal + laws, "plan", fn, keep_summary=False, extra_env=env)
        if res.status != "done" or project.task(task_id)["status"] == "stuck":
            return project.task(task_id)["status"]
        p = project.ledger.append("plan.recorded", "maker", "", task=task_id, text=res.summary)
        say("checker reviewing plan")
        ve, dis = review(project, task_id, checker, "plan", res.summary, plan=p["id"])
        say(f"checker: {ve['data']['verdict']}")
        if dis:
            say(f"disagreement {dis['id']} is in your inbox")
            return project.task(task_id)["status"]

    build_goal = goal + laws
    agreed = approved_plan(project, task_id)
    if agreed:
        build_goal = f"{build_goal}\n\nFollow this approved plan:\n{agreed}"

    say("maker building")
    before = guard.fingerprint(project.root)
    fn = make_permission_fn(project, task_id, wt, poll=poll, on_wait=on_wait)
    res = _make(project, task_id, maker, build_goal, "build", fn, keep_summary=not readonly, extra_env=env)
    say(f"maker: {res.status}")

    # backstop for invariant 9: whatever got past the gate, the human hears about it
    problems = []
    touched = guard.protected_in(project.diff(task_id, "--name-only").splitlines())
    if touched:
        problems.append(f"diff touches protected files: {', '.join(touched)}")
    after = guard.fingerprint(project.root)
    if after != before and not rules.trail_ok(project, before, after):
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
    if readonly:  # an investigator: its report is the result, there's no diff to check
        project.ledger.append("report.recorded", "maker", "", task=task_id, text=res.summary)
        return project.task(task_id)["status"]

    say("checker reviewing diff")
    ve, dis = review(project, task_id, checker, "diff", diff_material(project, task_id))
    say(f"checker: {ve['data']['verdict']}")
    if dis:
        say(f"disagreement {dis['id']} is in your inbox")
    return project.task(task_id)["status"]
