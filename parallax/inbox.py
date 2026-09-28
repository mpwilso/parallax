"""The inbox, batched for a human: grouped, each item with the conductor's recommendation.

Recommendations are shown, never applied. Every item still needs your call and a reason.
"""
from __future__ import annotations

from .agents.base import Conductor, Proposal
from .core import ParallaxError, Project, refuse_inside_task
from . import mission as missions


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


def resolve_item(project: Project, item_id: str, approve: bool, reason: str) -> tuple[dict, dict | None]:
    """Resolve one inbox item. Approving a proposal also creates and queues its task."""
    item = {e["id"]: e for e in project.inbox()}.get(item_id)
    if item and item["kind"] == "proposal.raised" and item["data"]["profile"] not in project.policy.profiles:
        raise ParallaxError(f"proposal {item_id} uses unknown profile {item['data']['profile']!r}")
    res = project.resolve(item_id, approve, reason)
    task = None
    if approve and item["kind"] == "proposal.raised":
        d = item["data"]
        task = project.new_task(item["reason"], profile=d["profile"], plan=d["plan"], queue=True, proposal=item_id)
    return res, task


def recommendations(project: Project) -> dict[str, dict]:
    """The latest conductor recommendation for each item."""
    out: dict[str, dict] = {}
    for e in project.ledger.entries():
        if e["kind"] == "recommendation.recorded":
            out[e["data"]["item"]] = e
    return out


def batched(project: Project) -> list[tuple[str, list[dict]]]:
    """Inbox items grouped: one group per task, then proposals."""
    tasks = project.tasks()
    groups: dict[str, list[dict]] = {}
    for e in project.inbox():
        tid = e["data"].get("task")
        if tid in tasks:
            t = tasks[tid]
            header = f"task {tid}  [{t['status']}]  {t['goal']}"
        else:
            header = "proposals" if e["kind"] == "proposal.raised" else "other"
        groups.setdefault(header, []).append(e)
    return sorted(groups.items(), key=lambda kv: kv[0] == "proposals")


def label(e: dict) -> str:
    d = e["data"]
    if e["kind"] == "disagreement.raised":
        return f"disagreement ({d['stage']})"
    if e["kind"] == "stuck.raised":
        return "stuck"
    if e["kind"] == "proposal.raised":
        return f"new task ({d['profile']}{', plan' if d['plan'] else ''})"
    return d["action"]
