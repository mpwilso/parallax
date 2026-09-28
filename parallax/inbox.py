"""The inbox, batched for a human: grouped, each item with the conductor's recommendation.

Recommendations are shown, never applied. Every item still needs your call and a reason.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import mission as missions
from . import rules
from .agents.base import Conductor, Proposal
from .core import ParallaxError, Project, refuse_inside_task
from .policy import ALLOW

RULE_KINDS = ("promotion.raised", "law.raised")


def record_proposal(project: Project, p: Proposal, **refs) -> dict:
    """A task the conductor thinks is worth doing. Lands in the inbox; nothing happens without a yes."""
    why, profile = p.why, p.profile
    if profile not in project.policy.profiles:
        why = f"{why} (asked for unknown profile {profile!r}, using default)".strip()
        profile = "default"
    return project.ledger.append("proposal.raised", "conductor", p.goal,
                                 why=why, profile=profile, plan=p.plan, **refs)


def split_goal(project: Project, conductor: Conductor, goal: str) -> list[dict]:
    """The conductor splits a goal into task proposals for you to approve."""
    refuse_inside_task(project.root)
    g = project.ledger.append("goal.received", "human", goal)
    mission = missions.load(project.root)
    try:
        proposals = conductor.split(mission.text if mission else "", goal)
    except Exception as err:
        project.ledger.append("goal.failed", "parallax", f"{type(err).__name__}: {err}", goal=g["id"])
        raise ParallaxError(f"conductor failed: {err}") from err
    return [record_proposal(project, p, source="goal", goal=g["id"]) for p in proposals]


@dataclass
class Outcome:
    entry: dict              # the decision.resolved entry
    task: dict | None = None  # a task created from an approved proposal
    changed: str = ""         # what an approved promotion or law changed


def resolve_item(project: Project, item_id: str, approve: bool, reason: str) -> Outcome:
    """Resolve one inbox item, applying what an approval means.

    Proposals become queued tasks. Promotions and laws are written into the rules files, and
    only if that edit verifies does the item get resolved.
    """
    refuse_inside_task(project.root)
    if not reason.strip():
        raise ParallaxError("a decision needs a reason")
    item = {e["id"]: e for e in project.inbox()}.get(item_id)
    if item is None:
        raise ParallaxError(f"no pending decision {item_id}")
    d = item["data"]
    if approve and item["kind"] == "proposal.raised" and d["profile"] not in project.policy.profiles:
        raise ParallaxError(f"proposal {item_id} uses unknown profile {d['profile']!r}")

    changed = ""
    if approve and item["kind"] == "promotion.raised":
        changed = rules.set_policy_rule(project, d["profile"], d["action"], d["key"], ALLOW, item_id)
    res = project.resolve(item_id, approve, reason)
    task = None
    if approve and item["kind"] == "proposal.raised":
        task = project.new_task(item["reason"], profile=d["profile"], plan=d["plan"], queue=True, proposal=item_id)
    return Outcome(res, task, changed)


def recommendations(project: Project) -> dict[str, dict]:
    """The latest conductor recommendation for each item."""
    out: dict[str, dict] = {}
    for e in project.ledger.entries():
        if e["kind"] == "recommendation.recorded":
            out[e["data"]["item"]] = e
    return out


RULES_GROUP, PROPOSALS_GROUP = "promotions and laws", "proposals"


def batched(project: Project) -> list[tuple[str, list[dict]]]:
    """Inbox items grouped: one group per task, then promotions and laws, then proposals."""
    tasks = project.tasks()
    groups: dict[str, list[dict]] = {}
    for e in project.inbox():
        tid = e["data"].get("task")
        if tid in tasks:
            t = tasks[tid]
            header = f"task {tid}  [{t['status']}]  {t['goal']}"
        elif e["kind"] in RULE_KINDS:
            header = RULES_GROUP
        else:
            header = PROPOSALS_GROUP if e["kind"] == "proposal.raised" else "other"
        groups.setdefault(header, []).append(e)
    order = {RULES_GROUP: 1, PROPOSALS_GROUP: 2}
    return sorted(groups.items(), key=lambda kv: order.get(kv[0], 0))


def label(e: dict) -> str:
    d = e["data"]
    if e["kind"] == "disagreement.raised":
        return f"disagreement ({d['stage']})"
    if e["kind"] == "stuck.raised":
        return "stuck"
    if e["kind"] == "proposal.raised":
        return f"new task ({d['profile']}{', plan' if d['plan'] else ''})"
    if e["kind"] == "promotion.raised":
        return f"promote ({d['profile']}) {d['action']}: {d['key']}"
    if e["kind"] == "law.raised":
        return "law"
    return d["action"]


def details(e: dict) -> list[str]:
    """Extra lines shown under an item in the inbox."""
    d = e["data"]
    if e["kind"] == "proposal.raised" and d.get("why"):
        return [f"why: {d['why']}"]
    if e["kind"] == "promotion.raised":
        return [f"evidence: {d['approvals']} approvals, 0 rejections"]
    if e["kind"] == "law.raised":
        lines = []
        if d.get("rule"):
            r = d["rule"]
            target = f"{r['action']} {r['key']}" if r.get("key") else r["action"]
            lines.append(f"rule: {r['ruling']} {target} ({r['profile']})")
        lines.append(f"evidence: {len(d['evidence'])} rejections ({', '.join(d['evidence'])})")
        return lines
    return []
