"""Decision needed: one question, its options, a recommendation, and what it blocks.

Whatever is waiting on you for a task becomes exactly one decision. The recommendation is
written by code, one rule per kind of decision, never by a model. `parallax decide <task>
<option>` answers it; options that override the check, accept a risk, redraft or drop need a
reason, and the rest don't.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from . import costs, lifecycle, pilot, status
from .core import ParallaxError, Project


@dataclass
class Option:
    name: str
    does: str
    needs_reason: bool = False


@dataclass
class Decision:
    kind: str
    question: str        # ends with "?"
    options: list[Option]
    recommend: str       # one of the option names
    blocks: str
    item: dict | None = None  # the ledger entry it settles, if there is one
    extra: dict = field(default_factory=dict)

    def option(self, name: str) -> Option:
        for o in self.options:
            if o.name == name:
                return o
        raise ParallaxError(f"{name} isn't an option here. choose from: {', '.join(o.name for o in self.options)}")


REJECT = Option("reject", "the drafters redraft the intent and plan from your reason", True)
DROP = Option("drop", "ends the task; it leaves the inbox", True)
RETRY = Option("retry", "runs it again from where it stopped")


def _open_item(project: Project, task_id: str) -> dict | None:
    items = [e for e in project.inbox() if e["data"].get("task") == task_id]
    return items[-1] if items else None


def raise_to(project: Project, task_id: str) -> float:
    """The cap to propose: what's spent, plus the plan's estimate again, and at least a dollar more."""
    plan = lifecycle.plan_data(project, task_id) or {}
    cap, _ = costs.budget(project, task_id, plan) if plan else (0.0, 0.0)
    return round(max(cap + 1.0, costs.spent(project, task_id) + float(plan.get("estimated_cost_usd", 1.0))), 2)


def decision(project: Project, task_id: str) -> Decision | None:
    """The one decision waiting on you for this task, or None."""
    t = project.task(task_id)
    item = _open_item(project, task_id)
    if item is None and t["status"] == "needs you":
        asked = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] == "review.requested"]
        why = asked[-1]["reason"] if asked else ""
        cited = {"why": why, "asked": asked[-1]["id"] if asked else ""}
        plan = lifecycle.plan_data(project, task_id) or {}
        cap = costs.budget(project, task_id, plan)[0] if plan else 0.0
        if "auto_launch_usd" in why:
            return Decision("launch", f"Launch it, with a cap of ${cap:.2f}?",
                            [Option("launch", "starts the build now"), DROP], "launch", "the build",
                            extra=cited)
        return Decision("review", "Does the plan do what you want?",
                        [Option("approve", "starts the build now"), REJECT, DROP], "approve", "the build",
                        extra=cited)
    if item is None:
        return None

    d, why = item["data"], " ".join(item["reason"].split())
    stage = d.get("stage")
    if d.get("budget"):
        new = raise_to(project, task_id)
        return Decision("cap", f"Raise the cap to ${new:.2f} so it can finish?",
                        [Option("raise", f"raises the cap to ${new:.2f} and picks up where it stopped"), DROP],
                        "raise", "the rest of the build and check", item, {"to": new})
    if stage == "conflict":
        return Decision("conflict", "Your intent and your plan disagree: which one wins?",
                        [Option("intent", "the intent wins: the plan is redrafted to fit it"),
                         Option("plan", "the plan wins: that finding stops blocking, and the check runs again"), DROP],
                        "intent", "the check", item)
    if stage == "scope":
        return Decision("scope", "The change goes outside the approved plan: accept that, or redraft?",
                        [Option("accept", "accepts the risk for this exact change, and the check goes on", True),
                         REJECT, DROP], "reject", "the check", item)
    if stage == "check" and why.startswith("the check still fails after"):
        return Decision("rework", "The check kept failing after every rework: redraft, or accept it as it is?",
                        [REJECT, Option("accept", "accepts the risk and makes it Ready", True), DROP],
                        "reject", "Ready", item)
    if stage == "check" and why.startswith("checker error"):
        return Decision("checker", "The checker failed to answer: try it again?",
                        [RETRY, Option("accept", "accepts it unreviewed, as a risk", True), DROP],
                        "retry", "Ready", item)
    if stage == "check":  # the plan's tests couldn't run
        return Decision("tests", "The plan's tests couldn't run: try again once the cause is fixed?",
                        [RETRY, REJECT, DROP], "retry", "Ready", item)
    if stage == "guard":
        return Decision("guard", "The change touched a protected file: redraft it?", [REJECT, DROP],
                        "reject", "Ready", item)
    if why.startswith("drafting"):
        return Decision("drafting", "The drafters couldn't get the plan right: redraft with a hint from you?",
                        [REJECT, DROP], "reject", "the whole task", item)
    return Decision("stuck", "It stopped: run it again, or redraft?", [RETRY, REJECT, DROP], "retry",
                    "the whole task", item)


def line(dec: Decision) -> str:
    """The output shape's decision line."""
    return f"Decide: {dec.question} Recommend: {dec.recommend}. Blocks: {dec.blocks}."


def apply(project: Project, task_id: str, name: str, reason: str = "", spawn: Callable | None = None,
          preflight_runner=None) -> str:
    """Carry out your answer. Returns one lowercase line saying what happens now."""
    dec = decision(project, task_id)
    if dec is None:
        raise ParallaxError(f"nothing about task {task_id} is waiting on you")
    opt = dec.option(name)
    if opt.needs_reason and not reason.strip():
        raise ParallaxError(f"{name} needs a reason: --reason \"...\"")
    said = reason.strip() or f"chose {name}"

    if name == "drop":
        if dec.item:
            project.resolve(dec.item["id"], False, said)
        project.ledger.append("task.rejected", "human", said, task=task_id, was=project.task(task_id)["status"])
        return f"dropped {task_id}. it's out of the inbox."
    if name in ("reject", "intent"):
        if name == "intent":
            said = reason.strip() or f"the intent wins over the plan: {dec.item['reason']}"
        if dec.item:
            project.resolve(dec.item["id"], False, said)
        pilot.redraft(project, task_id, said, spawn)
        return f"redrafting {task_id} from your reason. it comes back to the inbox."
    if name in ("launch", "approve"):
        lifecycle.approve(project, task_id)
    elif name == "raise":
        project.ledger.append("budget.raised", "human", said, task=task_id,
                              amount_usd=round(dec.extra["to"] - costs.budget(project, task_id, lifecycle.plan_data(project, task_id))[0], 2))
        project.resolve(dec.item["id"], True, said)
    elif name == "accept":
        project.resolve(dec.item["id"], True, said)
        if dec.kind in ("rework", "checker"):
            return f"accepted the risk on {task_id}. it's Ready: parallax accept {task_id} commits it."
    elif dec.item:  # retry, plan
        project.resolve(dec.item["id"], True, said)
    mode = pilot.resume(project, task_id, spawn, preflight_runner)
    what = {"pilot": "drafting", "build": "building", "check": "checking"}[mode]
    return f"{what} {task_id} without you. it comes back to the inbox."

