"""`parallax do "<the work>"`: from a few plain words to exactly one inbox item, without you.

One detached pilot process per task, started with the scrubbed environment:
1. Drafters write the intent (and a spec for a large task) and the plan. Parallax normalizes
   what they write, lints it, and checks the plan against the intent by code (planfit.py). Any
   problem goes back to the drafter with the exact reasons, up to 2 times; a third failure comes
   to you as Decision needed.
2. The launch rule in the policy decides whether code approves the plan and starts: a small task
   whose cap is at most auto_launch_usd and that touches nothing in review_paths, with
   review_plans off. The approval is signed and names the rule. Anything else waits for you.
3. Preflight, the build, and the check run as before, rework included.
The task ends at Ready, or at one Decision needed.
"""
from __future__ import annotations

import os
import sys
from typing import Callable

from . import build, check, costs, lifecycle, lint, planfit, sandbox
from .core import Project, refuse_inside_task

MAX_REDRAFTS = 2


def intake(project: Project, work: str, spawn: Callable | None = None) -> dict:
    """Create the task and start its pilot. Returns at once."""
    refuse_inside_task(project.root)
    t = project.new_task(work, intent=True)
    home = sandbox.task_home(project.root, t["task"])
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    argv = [sys.executable, "-u", "-m", "parallax.build", str(project.root), t["task"], "pilot"]
    # your PATH stays, so the policy's setup command finds its tools; the maker's PATH is set on its own
    env = build.scrubbed_env(None, path=os.environ.get("PATH", build.SYSTEM_PATH))
    pid = (spawn or build._spawn)(argv, env, project.root, home / "pilot.log")
    project.ledger.append("build.started", "parallax", "", task=t["task"], pid=pid, mode="pilot")
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
        plan = lifecycle.plan_data(project, task_id)
        fit = planfit.problems(lifecycle._read(project, task_id, "intent"), plan,
                               costs.spent(project, task_id), project.policy.budget)
        if not fit:
            return "fit"
        problems = {"plan": fit}
        project.ledger.append("draft.misfit", "parallax", "; ".join(fit), task=task_id, attempt=attempt + 1)
    detail = "; ".join(p for ps in problems.values() for p in ps)
    return _needs_you(project, task_id, f"drafting still failed after {MAX_REDRAFTS} redrafts: {detail}")


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
    if cap > limit:
        return False, f"the budget cap (${cap:.2f}) is over auto_launch_usd (${limit:.2f})"
    return True, f"launch rule: a small task, cap ${cap:.2f} within auto_launch_usd ${limit:.2f}, nothing in review_paths"


def go(project: Project, task_id: str, maker_for, checker_for, test_runner=None, preflight_runner=None) -> str:
    """After an approval, by the rule or by you: preflight, build, check."""
    p = build.prepare(project, task_id, setup=True, launching=False)
    if not all(line.ok for line in build.run_preflight(project, p, preflight_runner)):
        return _needs_you(project, task_id, "preflight failed, so the build didn't launch")
    status = build.run_build(project, task_id, maker_for)
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
