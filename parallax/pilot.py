"""`parallax do "<the work>"`: from a few plain words to exactly one inbox item, without you.

One detached pilot process per task, started with the scrubbed environment:
1. Drafters write the intent (and a spec for a large task) and the plan. Parallax normalizes
   what they write, lints it, and checks the plan against the intent by code (planfit.py). Any
   problem goes back to the drafter with the exact reasons, up to 2 times; a third failure comes
   to you as Decision needed.
2. The launch rule in the policy decides whether code approves the plan and starts: a small task
   whose cap is at most auto_launch_usd, that touches nothing in review_paths, and that crosses
   no boundary (no domains, no outside_reads), with review_plans off. The approval is signed and
   names the rule. Anything else waits for you.
3. Preflight, the build, and the check run as before, rework included.
The task ends at Ready, or at one Decision needed.
"""
from __future__ import annotations

import math
import os
import re
import sys
from typing import Callable

from . import build, check, costs, lifecycle, lint, planfit, reticle, sandbox, status
from .core import ParallaxError, Project, refuse_inside_task

MAX_REDRAFTS = 2


def intake(project: Project, work: str, spawn: Callable | None = None) -> dict:
    """Create the task and start its pilot. Returns at once."""
    refuse_inside_task(project.root)
    t = project.new_task(work, intent=True)
    _spawn_pilot(project, t["task"], spawn)
    return project.task(t["task"])


def _needs_you(project: Project, task_id: str, why: str) -> str:
    project.ledger.append("stuck.raised", "parallax", why, task=task_id)
    return "stuck"


def draft_until_fit(project: Project, task_id: str, drafter_for) -> str:
    """Draft, normalize, lint and check the plan against the intent. Returns "fit" or "stuck"."""
    problems: dict[str, list[str]] = {}
    for attempt in range(MAX_REDRAFTS + 1):
        st = lifecycle.state(project, task_id)
        todo = [d for d in ("intent", "spec", "plan") if d in (st.gate or ())]
        if st.gate and "intent" in st.gate and attempt and "intent" not in problems:
            todo.remove("intent")  # a good intent isn't redrafted to fix a plan
        if not lifecycle.draft(project, task_id, todo, drafter_for, problems):
            why = lifecycle.state(project, task_id).failed[1]
            return _needs_you(project, task_id, lint.one_sentence(f"drafting stopped: {why}"))
        problems = {}
        for doc in todo:
            found = [m for _, m in lint.lint_lifecycle(lifecycle._read(project, task_id, doc), doc)]
            if found:
                problems[doc] = found
        if problems:
            continue
        if lint.intent_fields(lifecycle._read(project, task_id, "intent")).get("size") == "large" \
                and lifecycle.state(project, task_id).gate == ("intent",):
            lifecycle.approve(project, task_id, rule="large task: its intent is approved by code, its plan waits for you")
            return draft_until_fit(project, task_id, drafter_for)
        plan = _room_for_rework(project, task_id, lifecycle.plan_data(project, task_id))
        fit = planfit.problems(lifecycle._read(project, task_id, "intent"), plan,
                               costs.spent(project, task_id), project.policy.budget, _reserve(project, plan))
        if not fit:
            return "fit"
        problems = {"plan": fit}
        project.ledger.append("draft.misfit", "parallax", "; ".join(fit), task=task_id, attempt=attempt + 1)
    detail = "; ".join(p for ps in problems.values() for p in ps)
    return _needs_you(project, task_id, f"drafting still failed after {MAX_REDRAFTS} redrafts: {detail}")


def _reserve(project: Project, plan: dict) -> float:
    """What the cap keeps for Field and Reticle when they'll run: each one's limit."""
    from . import uitest
    field = float(uitest.settings(project)["max_usd"]) if uitest.applies(project, plan) else 0.0
    return field + (float(project.policy.reticle["max_usd"]) if project.policy.reticle["enabled"] else 0.0)


def _room_for_rework(project: Project, task_id: str, plan: dict) -> dict:
    """A cap too short is raised by code before approval, and the plan's hash recorded again.

    Two floors, the higher wins: room for one rework round (drafting so far plus twice the
    estimate), and what Maker has really spent on tasks of this size (capfloor.py) plus drafting so
    far. Only Parallax knows what drafting cost once the plan is written, so it sets them. Never past
    the size's limit, and never when the intent names a budget: that's yours, and a short one comes
    back as a misfit. The launch rule then judges the raised cap, so one over auto_launch_usd asks you."""
    from . import capfloor
    intent = lifecycle._read(project, task_id, "intent")
    if lint.budget_of(intent) is not None:
        return plan
    spent, reserve = costs.spent(project, task_id), _reserve(project, plan)
    rework = planfit.rework_floor(float(plan["estimated_cost_usd"]), spent, reserve)
    size = lint.intent_fields(intent).get("size", "small")
    limit = float(project.policy.budget["large_cap_usd" if size == "large" else "small_cap_usd"])
    if rework > limit:  # no cap fits: planfit tells the drafter the most it may estimate
        return plan
    maker, source, where = capfloor.floor(project, size, exclude=task_id)
    history = round(spent + maker + reserve, 2)
    floor = max(rework, history)
    cap = float(plan["budget_cap_usd"])
    new = min(math.ceil(floor * 10 - 1e-9) / 10, limit)  # up to the next ten cents, never past the limit
    if cap >= new:
        return plan
    path = lifecycle.doc_path(project, task_id, "plan")
    text = path.read_text(encoding="utf-8")
    text, n = re.subn(r"(?m)^budget_cap_usd\s*=.*$", f"budget_cap_usd = {new:.2f}", text)
    if n != 1:
        return plan
    path.write_text(text, encoding="utf-8")
    if history > rework:
        why = (f"raised the cap from ${cap:.2f} to ${new:.2f}: {source} is ${maker:.2f}, plus drafting so far "
               f"${spent:.2f}" + (f" and Field's ${reserve:.2f}" if reserve else "") + f", needs ${history:.2f}")
    else:
        why = (f"raised the cap from ${cap:.2f} to ${new:.2f}: drafting spent ${spent:.2f}, and the work twice "
               f"plus any UI tester share needs ${rework:.2f}")
    project.ledger.append("draft.recorded", "parallax", why, task=task_id, doc="plan", sha=lifecycle.file_hash(path),
                          floor="history" if history > rework else "rework", maker_floor=maker, floor_from=where)
    return lifecycle.plan_data(project, task_id)


def launch_rule(project: Project, task_id: str) -> tuple[bool, str]:
    """(code may launch, why). The why names the rule; it's recorded with the approval or the request."""
    intent = lifecycle._read(project, task_id, "intent")
    plan = lifecycle.plan_data(project, task_id)
    rules = project.policy.launch
    cap, limit = float(plan["budget_cap_usd"]), float(rules["auto_launch_usd"])
    if rules["review_plans"]:
        return False, "review_plans is on in the policy, so every plan waits for you"
    if lint.intent_fields(intent).get("size") == "large":
        return False, "the intent says size: large, so its plan waits for you"
    touched = [p for p in [*plan["files"], *plan["tests"]] if planfit.in_scope(p, rules["review_paths"])]
    if touched:
        return False, f"the plan touches {touched[0].split('::')[0]}, which is in review_paths"
    for field in ("domains", "outside_reads"):  # a drafter steered by repo text can't open the boundary alone
        if plan[field]:
            return False, f"the plan's {field} ({plan[field][0]}) cross the boundary, so only you can approve it"
    if cap > limit:
        return False, f"the budget cap (${cap:.2f}) is over auto_launch_usd (${limit:.2f})"
    return True, f"launch rule: a small task, cap ${cap:.2f} within auto_launch_usd ${limit:.2f}, nothing in review_paths"


def go(project: Project, task_id: str, maker_for, checker_for, test_runner=None, preflight_runner=None) -> str:
    """After an approval, by the rule or by you: setup, then the build (which preflights first), then the check."""
    p = build.prepare(project, task_id, setup=True, launching=False)  # the venv, once, as you
    reticle.write(project, task_id, p, runner=test_runner)  # tests of the outcomes, before Maker; off by default
    status = build.run_build(project, task_id, maker_for, preflight_runner=preflight_runner)
    if status == "built":
        status = check.run_check(project, task_id, checker_for, maker_for,
                                 test_runner=test_runner, preflight_runner=preflight_runner)
    return status


def run(project: Project, task_id: str, drafter_for, maker_for, checker_for, *,
        test_runner=None, preflight_runner=None) -> str:
    project.ledger.append("pilot.started", "parallax", "", task=task_id)
    if draft_until_fit(project, task_id, drafter_for) != "fit":
        return "stuck"
    auto, why = launch_rule(project, task_id)
    if not auto:
        project.ledger.append("review.requested", "parallax", why, task=task_id)
        return "needs you"
    lifecycle.approve(project, task_id, rule=why)
    return go(project, task_id, maker_for, checker_for, test_runner, preflight_runner)


def _spawn_pilot(project: Project, task_id: str, spawn: Callable | None = None) -> int:
    """A detached pilot. Your PATH stays, so the policy's setup command finds its tools; the maker's
    PATH is set on its own."""
    home = sandbox.task_home(project.root, task_id)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    argv = [sys.executable, "-P", "-u", "-m", "parallax.build", str(project.root), task_id, "pilot"]
    env = build.scrubbed_env(None, path=os.environ.get("PATH", build.SYSTEM_PATH))
    pid = (spawn or build._spawn)(argv, env, project.root, home / "pilot.log")
    project.ledger.append("build.started", "parallax", "", task=task_id, pid=pid, mode="pilot")
    return pid


def redraft(project: Project, task_id: str, reason: str, spawn: Callable | None = None) -> dict:
    """Your reject at Ready: a new attempt. The drafters get your reason and may redraft the intent
    as well as the plan; the worktree goes back to its base; the pilot runs again, with a fresh cap."""
    refuse_inside_task(project.root)
    if not reason.strip():
        raise ParallaxError("a reject needs a reason: it's what Focus redrafts from")
    if task_id in build.running_builds(project):
        raise ParallaxError(f"task {task_id} is working right now. parallax stop ends it first")
    t = lifecycle.lifecycle_task(project, task_id)
    e = project.ledger.append("task.redraft", "human", reason, task=task_id)
    from . import since
    since.keep(project, task_id, sum(x["kind"] == "task.redraft" and x["data"].get("task") == task_id
                                     for x in project.ledger.entries()))  # before the drafters replace it
    wt = t["worktree"]
    import subprocess
    subprocess.run(["git", "-C", wt, "reset", "-q", "--hard", t["base"]], capture_output=True)
    subprocess.run(["git", "-C", wt, "clean", "-fdq"], capture_output=True)
    _spawn_pilot(project, task_id, spawn)
    return e


def resume(project: Project, task_id: str, spawn: Callable | None = None, preflight_runner=None) -> str:
    """Start a task again where it stopped: drafting, the build, or the check. Returns the mode."""
    if lifecycle.state(project, task_id).gate is not None:
        _spawn_pilot(project, task_id, spawn)
        return "pilot"
    built = any(e["kind"] == "build.finished" for e in status.attempt(project.ledger.entries(), task_id))
    mode = "check" if built else "build"
    build.launch(project, build.prepare(project, task_id), spawn=spawn, mode=mode)  # the builder preflights first
    return mode
