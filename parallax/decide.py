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

    @property
    def owner(self) -> str:
        """Whose call this is, by kind: security for a secrets or protected-path item, else you."""
        return "security" if self.kind == "guard" or (self.kind == "scope" and self.extra.get("secret")) else "you, as the engineer"

    @property
    def why_human(self) -> str:
        return WHY_HUMAN[self.kind] if self.kind != "scope" or not self.extra.get("secret") else \
            "a file that looks like a secret has content, and no rule lets code accept that"


WHY_HUMAN = {  # one short sentence per kind: why code stopped instead of deciding
    "launch": "the cap is over your launch limit, and money is yours to spend",
    "review": "your policy says a plan like this waits for your review",
    "cap": "the cap you approved is spent, and raising it is spending more",
    "conflict": "two things you approved disagree, and only you can say which one you meant",
    "scope": "the change reached outside the plan you approved",
    "flows": "only a person can say whether the test or the app is wrong",
    "rework": "three reworks didn't satisfy the check, so the plan or the work needs your judgment",
    "checker": "Second Eye failed twice, so nothing has reviewed this change",
    "tests": "the tests couldn't run, which is a setup problem, not Maker's work",
    "guard": "a protected path was touched, and no rule lets code accept that",
    "drafting": "Focus couldn't produce a plan that fits, and only you can restate the work",
    "error": "it stopped on an error nobody planned for",
    "stuck": "it stopped, and whether to try again or change course is yours",
    "turns": "Maker used every turn it had, and more turns may just be more of the same",
}



REJECT = Option("reject", "Focus redrafts the intent and plan from your reason", True)
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
    if d.get("turns"):
        return Decision("turns", "Maker used every turn it had without finishing: run it again, or redraft?",
                        [RETRY, REJECT, DROP], "retry", "the rest of the build and check", item)
    if d.get("budget"):
        new = raise_to(project, task_id)
        return Decision("cap", f"Raise the cap to ${new:.2f} so it can finish?",
                        [Option("raise", f"raises the cap to ${new:.2f} and picks up where it stopped"), DROP],
                        "raise", "the rest of the build and check", item, {"to": new})
    if stage == "conflict" and d.get("missing"):
        return Decision("conflict", f"The plan's test file {d['missing'][0]} is missing from the change: redraft?",
                        [REJECT, DROP], "reject", "the check", item)
    if stage == "conflict":
        return Decision("conflict", "Your intent and your plan disagree: which one wins?",
                        [Option("intent", "the intent wins: the plan is redrafted to fit it"),
                         Option("plan", "the plan wins: that finding stops blocking, and the check runs again"), DROP],
                        "intent", "the check", item)
    if stage == "scope":
        secret = any(f.get("secret") and f.get("size") for f in d.get("files") or [])
        question = ("The change holds a secrets file with content: accept that, or redraft?" if secret else
                    "The change goes outside the approved plan: accept that, or redraft?")
        return Decision("scope", question,
                        [Option("accept", "accepts the risk for this exact change, and the check goes on", True),
                         REJECT, DROP], "reject", "the check", item, {"secret": secret})
    if stage == "flows":
        return Decision("flows", "Field's UI test still fails after a rework: is the test wrong, or the app?",
                        [Option("remove", "the test is wrong: it's taken out of this task, and the check goes on", True),
                         Option("reject", "the app is wrong: Focus redrafts the intent and plan from your reason", True),
                         DROP], "reject", "Ready", item)
    if stage == "check" and why.startswith("the check still fails after"):
        return Decision("rework", "The check kept failing after every rework: redraft, or accept it as it is?",
                        [REJECT, Option("accept", "accepts the risk and makes it Ready", True), DROP],
                        "reject", "Ready", item)
    if stage == "check" and why.startswith("Second Eye error"):
        return Decision("checker", "Second Eye failed to answer, twice: try it again?",
                        [RETRY, Option("accept", "accepts it unreviewed, as a risk", True), DROP],
                        "retry", "Ready", item)
    if stage == "check":  # the plan's tests couldn't run
        return Decision("tests", "The plan's tests couldn't run: try again once the cause is fixed?",
                        [RETRY, REJECT, DROP], "retry", "Ready", item)
    if stage == "guard":
        return Decision("guard", "The change touched a protected file: redraft it?", [REJECT, DROP],
                        "reject", "Ready", item)
    if why.startswith("drafting"):
        return Decision("drafting", "Focus couldn't get the plan right: redraft with a hint from you?",
                        [REJECT, DROP], "reject", "the whole task", item)
    if d.get("error"):  # the same error twice means running it again won't help
        again = sum(e["kind"] == "stuck.raised" and " ".join(e["reason"].split()) == why
                    for e in project.ledger.entries() if e["data"].get("task") == task_id) > 1
        return Decision("error", "It stopped on an error: run it again once the cause is fixed, or drop it?",
                        [RETRY, REJECT, DROP], "drop" if again else "retry", "the whole task", item)
    return Decision("stuck", "It stopped: run it again, or redraft?", [RETRY, REJECT, DROP], "retry",
                    "the whole task", item)


SANDBOX_START = ("sandbox runtime", "srt:", "bwrap", "namespace")  # words of a sandbox that never started
NAMESPACES = ("namespace", "bwrap")  # what docs/wsl.md's user-namespace step is about


def sandbox_hint(why: str) -> list[str]:
    """One or two lines for an error card, when what stopped is the sandbox starting.

    The first names `parallax doctor`, which already checks the sandbox tools. A namespace or bwrap
    error is the one Ubuntu 24.04 causes, so that one also points at the step that allows them.
    """
    low = why.lower()
    if not any(s in low for s in SANDBOX_START):
        return []
    hint = ["Run parallax doctor to find the cause."]
    if any(s in low for s in NAMESPACES):
        hint.append('For the user-namespace step, see "Allow user namespaces" in docs/wsl.md.')
    return hint


def line(dec: Decision) -> str:
    """The output shape's decision line."""
    return f"Decide: {dec.question} Recommend: {dec.recommend}. Blocks: {dec.blocks}."


def lines(dec: Decision) -> list[str]:
    """The Decisions section: the question, then whose call it is and why a human, set by code."""
    return [line(dec), f"Whose call: {dec.owner}.", f"Why a human: {dec.why_human}."]


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
    elif name == "remove":
        from . import uitest
        uitest.remove(project, task_id, dec.item["data"].get("files") or [], said)
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

