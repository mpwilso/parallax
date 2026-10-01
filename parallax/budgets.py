"""The budget rules that are yours: a budget you name over the policy's limit asks you once, and a
redraft never lowers a cap you already approved.

- over_limit: the intent names a budget above the size's limit (or what you allowed for this task).
  Drafting stops once with a decision, allow it or use the limit, instead of redrafting into a misfit
  it can never fix (786e71).
- approved_cap: the highest cap you approved in an earlier attempt, raises included. A redraft's cap
  is lifted to it by code, never past the limit (fb461d went $3.80, $3.60, $2.50).
- the mode, chosen once ([budget] mode, asked at init or on the first UI open): "ask" stops at each
  plan's cap, under the size caps, as before; "ceiling" keeps going and stops only at ceiling_usd a
  task; "none" never stops for money. A budget named in the request always wins: it is the cap, and
  above the mode's limit it asks once (over_limit). In every mode, the same check failing the same
  way twice in a row stops and asks (check.py), so no mode can spend in a loop.
"""
from __future__ import annotations

import math
import re

from . import lifecycle, lint
from .core import Project


def money(x: float) -> str:
    return f"${x:.2f}".removesuffix(".00")


NO_LIMIT = math.inf
QUESTION = "How should Parallax handle spending?"
NOTE = ("Costs are Claude Code's estimates at API list prices. On a Claude subscription they measure how much "
        "a task used, not a charge.")


def mode(project: Project) -> str:
    return project.policy.budget["mode"]


def choices(project: Project) -> list[dict]:
    """The question's options, in the policy's own numbers: {mode, label, does}."""
    b = project.policy.budget
    return [
        {"mode": "ask", "label": "Ask me before a task goes over a limit",
         "does": f"the default: each task has a cap, at most {money(b['small_cap_usd'])} for a small task and "
                 f"{money(b['large_cap_usd'])} for a large one, and it stops and asks before going over"},
        {"mode": "ceiling", "label": f"Keep going, and stop only at {money(b['ceiling_usd'])} a task",
         "does": f"no question about money until a task has used {money(b['ceiling_usd'])}"},
        {"mode": "none", "label": "No limit",
         "does": "it never stops for money; it still stops and asks when the same check fails the same way twice in a row"},
    ]


def describe(project: Project) -> str:
    """The current mode in one sentence, and how to change it: for the overview and parallax budget."""
    label = next(c["label"] for c in choices(project) if c["mode"] == mode(project))
    chosen = "" if project.policy.mode_chosen else " (the default; nobody has chosen yet)"
    return (f"Spending: {label[:1].lower()}{label[1:]}{chosen}. Change it with parallax budget ask, ceiling or none, "
            f"or [budget] mode in parallax.policy.toml.")


def choose(project: Project, chosen: str, reason: str = "") -> dict:
    """Your answer, written to the local policy file and recorded as yours."""
    from .core import POLICY_FILE
    from .policy import write_budget_mode
    write_budget_mode(project.root / POLICY_FILE, chosen)
    project.reload_policy()
    return project.ledger.append("budget.mode", "human", reason or f"chose {chosen}", mode=chosen,
                                 ceiling_usd=project.policy.budget["ceiling_usd"])


def state(project: Project) -> dict:
    """What the page needs: the mode, whether anyone chose it, and the question with its options."""
    return {"mode": mode(project), "chosen": project.policy.mode_chosen, "question": QUESTION, "note": NOTE,
            "choices": choices(project), "line": describe(project)}


def mode_limit(project: Project, size: str) -> float:
    """The most a plan may set for a task of this size, by the mode."""
    b = project.policy.budget
    if mode(project) == "none":
        return NO_LIMIT
    if mode(project) == "ceiling":
        return float(b["ceiling_usd"])
    return float(b["large_cap_usd" if size == "large" else "small_cap_usd"])


def effective_cap(project: Project, task_id: str, plan_cap: float) -> float:
    """What the task may spend before it stops: a budget you named, else what the mode says."""
    if lint.budget_of(lifecycle._read(project, task_id, "intent")) is not None or mode(project) == "ask":
        return plan_cap  # the plan's cap is your named budget (planfit holds it to that), or the mode's cap
    if mode(project) == "none":
        return NO_LIMIT
    return max(plan_cap, float(project.policy.budget["ceiling_usd"]))


def launch_limit(project: Project) -> float:
    """The cap code may launch with, under the policy: auto_launch_usd, or the mode's ceiling."""
    auto = float(project.policy.launch["auto_launch_usd"])
    return {"ask": auto, "ceiling": max(auto, float(project.policy.budget["ceiling_usd"])), "none": NO_LIMIT}[mode(project)]


def shown(cap: float | None) -> str:
    """A cap in words: dollars, or no limit."""
    return "no limit" if cap is None or math.isinf(cap) else f"${cap:.2f}"


def size_of(project: Project, task_id: str) -> str:
    return lint.intent_fields(lifecycle._read(project, task_id, "intent")).get("size", "small")


def _mine(project: Project, task_id: str) -> list[dict]:
    return [e for e in project.ledger.entries() if e["data"].get("task") == task_id]


def limit(project: Project, task_id: str, size: str | None = None) -> float:
    """The policy's cap for the task's size, or the budget you allowed for this task, if higher."""
    size = size or size_of(project, task_id)
    base = mode_limit(project, size)
    allowed = [e for e in _mine(project, task_id) if e["kind"] == "budget.allowed"]
    return max(base, float(allowed[-1]["data"]["amount_usd"])) if allowed else base


def policy(project: Project, task_id: str) -> dict:
    """The budget policy as it applies to this task: the size's limit is what limit() says."""
    size = size_of(project, task_id)
    return {**project.policy.budget, ("large_cap_usd" if size == "large" else "small_cap_usd"): limit(project, task_id, size)}


def over_limit(project: Project, task_id: str) -> dict | None:
    """{named, limit, size} when the intent names a budget over the limit, else None."""
    intent = lifecycle._read(project, task_id, "intent")
    named, size = lint.budget_of(intent), size_of(project, task_id)
    cap = limit(project, task_id, size)
    return {"named": named, "limit": cap, "size": size} if named is not None and named > cap + 0.005 else None


def stop_reason(over: dict) -> str:
    return f"your budget of {money(over['named'])} is over the {money(over['limit'])} limit for {over['size']} tasks"


def just_decided(project: Project, task_id: str) -> bool:
    """Your answer to the over-limit question is newer than the last draft: drafting goes on from the
    drafts there are, without writing them again."""
    for e in reversed(_mine(project, task_id)):
        if e["kind"] in ("budget.allowed", "budget.limited"):
            return True
        if e["kind"] in ("draft.recorded", "task.redraft"):
            return False
    return False


def launch_answer(project: Project, task_id: str) -> dict | None:
    """Your "allow and launch", when it still holds: the last budget answer this attempt, and the plan
    is byte for byte the one you could read when you gave it. Then the launch is yours, already given."""
    from .status import attempt
    answers = [e for e in attempt(project.ledger.entries(), task_id) if e["kind"] in ("budget.allowed", "budget.limited")]
    if not answers or not answers[-1]["data"].get("launch"):
        return None
    plan = lifecycle.doc_path(project, task_id, "plan")
    if not plan.exists() or lifecycle.file_hash(plan) != answers[-1]["data"].get("plan_sha"):
        return None
    return answers[-1]


def use_limit(project: Project, task_id: str, cap: float, reason: str) -> None:
    """You chose the limit: the intent's budget and the plan's cap become it, written by code, and
    each file's new hash is recorded, as when code raises a cap before approval."""
    for doc, pattern, line in (("intent", r"(?m)^budget:.*$", f"budget: {cap:.2f}"),
                               ("plan", r"(?m)^budget_cap_usd\s*=.*$", f"budget_cap_usd = {cap:.2f}")):
        path = lifecycle.doc_path(project, task_id, doc)
        if not path.exists():
            continue
        text, n = re.subn(pattern, line, path.read_text(encoding="utf-8"), count=1)
        if n:
            path.write_text(text, encoding="utf-8")
            project.ledger.append("draft.recorded", "parallax", f"used the {money(cap)} limit, as you chose", task=task_id,
                                  doc=doc, sha=lifecycle.file_hash(path))
    project.ledger.append("budget.limited", "human", reason, task=task_id, amount_usd=cap)


def _cap_in(text: str) -> float | None:
    data, _, why = lint.plan_block(text)
    try:
        return None if why else float(data["budget_cap_usd"])
    except (KeyError, TypeError, ValueError):
        return None


def approved_cap(project: Project, task_id: str) -> float:
    """The highest cap approved in an earlier attempt of this task, with what you raised it by. Each
    approval records its cap; for older ones, the plan kept at that reject (since.keep) says it."""
    from .since import _kept
    best, cap, raised, n = 0.0, None, 0.0, 0
    for e in _mine(project, task_id):
        if e["kind"] == "gate.approved" and "plan" in e["data"].get("gate", ""):
            cap, raised = e["data"].get("cap_usd", "kept"), 0.0
        elif e["kind"] == "budget.raised":
            raised += float(e["data"].get("amount_usd") or 0)
        elif e["kind"] == "task.redraft":
            n += 1
            if cap == "kept":
                kept = _kept(project, task_id, n) / "plan.md"
                cap = _cap_in(kept.read_text(encoding="utf-8")) if kept.exists() else None
            if cap is not None:
                best = max(best, float(cap) + raised)
            cap, raised = None, 0.0
    return round(best, 2)
