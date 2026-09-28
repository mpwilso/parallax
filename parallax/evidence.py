"""Evidence moves the line: approval history proposes promotions (computed here, by code).

Nothing here changes a rule. It only raises proposals that wait for a human.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .core import Project
from .policy import ALLOW, normalize_detail

Key = tuple[str, str, str]  # (profile, action, exact key)


def _ts(e: dict) -> datetime:
    return datetime.fromisoformat(e["ts"])


def _window(project: Project, now: datetime | None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=project.policy.limits["evidence_days"])


def stats(project: Project, now: datetime | None = None) -> dict[Key, dict]:
    """Per exact request: human approvals and rejections inside the evidence window."""
    since = _window(project, now)
    tasks = project.tasks()
    entries = project.ledger.entries()
    requests = {e["id"]: e for e in entries if e["kind"] == "decision.requested"}
    out: dict[Key, dict] = {}
    for pos, e in enumerate(entries):
        if e["kind"] != "decision.resolved" or e["data"]["decision"] not in requests or _ts(e) < since:
            continue
        req = requests[e["data"]["decision"]]
        t = tasks.get(req["data"]["task"], {})
        action = req["data"]["action"]
        key = req["data"].get("key") or normalize_detail(action, req["reason"], t.get("worktree"))
        s = out.setdefault((t.get("profile", "default"), action, key), {"approved": [], "rejected": []})
        s["approved" if e["data"]["outcome"] == "approved" else "rejected"].append((e["id"], pos))
    return out


def _promotions(project: Project) -> tuple[set[Key], dict[Key, int]]:
    """Keys with a pending promotion, and the ledger position of the last rejected promotion per key."""
    entries = project.ledger.entries()
    raised = {e["id"]: e for e in entries if e["kind"] == "promotion.raised"}
    pending = {e["id"] for e in project.inbox()}
    key = lambda e: (e["data"]["profile"], e["data"]["action"], e["data"]["key"])
    rejected = {}
    for pos, e in enumerate(entries):
        d = e["data"]
        if e["kind"] == "decision.resolved" and d["decision"] in raised and d["outcome"] == "rejected":
            rejected[key(raised[d["decision"]])] = pos
    return {key(e) for i, e in raised.items() if i in pending}, rejected


def status_of(project: Project, k: Key, s: dict, pending: set[Key], rejected: dict[Key, int]) -> str:
    profile, action, key = k
    need = project.policy.limits["promote_after"]
    if project.policy.ruling(action, profile, key) == ALLOW:
        return "allowed"
    if k in pending:
        return "promotion pending"
    if s["rejected"]:
        return "has rejections"
    if profile == "readonly":
        return "readonly, can't promote"
    approvals = [pos for _, pos in s["approved"] if k not in rejected or pos > rejected[k]]
    if len(approvals) >= need:
        return "candidate"
    return f"{need - len(approvals)} more to go"


def promotion_candidates(project: Project, now: datetime | None = None) -> list[tuple[Key, dict]]:
    pending, rejected = _promotions(project)
    return [(k, s) for k, s in stats(project, now).items()
            if status_of(project, k, s, pending, rejected) == "candidate"]


def raise_promotions(project: Project, now: datetime | None = None) -> list[dict]:
    days = project.policy.limits["evidence_days"]
    out = []
    for (profile, action, key), s in promotion_candidates(project, now):
        n = len(s["approved"])
        out.append(project.ledger.append(
            "promotion.raised", "parallax", f"approved {n} times, never rejected in {days} days: {action} {key}",
            profile=profile, action=action, key=key, evidence=[i for i, _ in s["approved"]], approvals=n,
        ))
    return out


def table(project: Project, now: datetime | None = None) -> list[tuple[Key, dict, str]]:
    """Most-decided requests first, with where each stands."""
    pending, rejected = _promotions(project)
    rows = [(k, s, status_of(project, k, s, pending, rejected)) for k, s in stats(project, now).items()]
    return sorted(rows, key=lambda r: -(len(r[1]["approved"]) + len(r[1]["rejected"])))
