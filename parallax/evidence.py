"""Evidence moves the line, both ways, and always through a human yes.

Promotions loosen: computed here, by code, from approval history.
Laws tighten: proposed by the conductor from rejection reasons, then checked here against the
ledger before they reach the inbox. A law that doesn't hold up is dropped and reported.
Nothing here changes a rule. It only raises items that wait for a human.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .agents.base import Law
from .core import Project
from .policy import ALLOW, ASK, DENY, STRICTNESS, normalize_detail

RULE_KINDS = ("promotion.raised", "law.raised")

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


# laws --------------------------------------------------------------------------------

def _raised(entries: list[dict]) -> dict[str, dict]:
    """Every inbox-kind entry by id, so a resolution can be traced to what it resolved."""
    return {e["id"]: e for e in entries if e["kind"].endswith((".requested", ".raised"))}


def _law_backed(project: Project) -> set[str]:
    """Rejection ids already behind a pending or approved law. Each backs one law at most."""
    entries = project.ledger.entries()
    laws = {e["id"]: e for e in entries if e["kind"] == "law.raised"}
    rejected_laws = {e["data"]["decision"] for e in entries
                     if e["kind"] == "decision.resolved" and e["data"]["decision"] in laws
                     and e["data"]["outcome"] == "rejected"}
    return {i for lid, e in laws.items() if lid not in rejected_laws for i in e["data"]["evidence"]}


def rejections(project: Project, now: datetime | None = None) -> list[tuple[dict, dict]]:
    """Recent human rejections usable as law evidence: (resolution, what it resolved)."""
    since = _window(project, now)
    entries = project.ledger.entries()
    raised, used = _raised(entries), _law_backed(project)
    out = []
    for e in entries:
        d = e["data"]
        if (e["kind"] == "decision.resolved" and d["outcome"] == "rejected" and _ts(e) >= since
                and d["decision"] in raised and raised[d["decision"]]["kind"] not in RULE_KINDS
                and e["id"] not in used):
            out.append((e, raised[d["decision"]]))
    return out


def check_law(project: Project, law: Law, now: datetime | None = None) -> str | None:
    """Why this law can't go to the inbox, or None if it holds up."""
    if not law.text.strip():
        return "it has no text"
    usable = {e["id"] for e, _ in rejections(project, now)}
    cited = list(dict.fromkeys(law.evidence))
    bad = [i for i in cited if i not in usable]
    if bad:
        return f"cites {', '.join(bad)}, not recent rejections free to back a law"
    need = project.policy.limits["law_after"]
    if len(cited) < need:
        return f"cites {len(cited)} rejections, needs {need}"
    if law.rule:
        return check_rule(project, law.rule)
    return None


def check_rule(project: Project, rule: dict) -> str | None:
    """A law's rule must name a real profile and strictly tighten what's there now."""
    profile, action, key, ruling = rule.get("profile", "default"), rule.get("action"), rule.get("key"), rule.get("ruling")
    if ruling not in (ASK, DENY):
        return f"rule ruling {ruling!r} isn't ask or deny, and laws only tighten"
    if not action or action == "git.merge":
        return f"rule action {action!r} can't be set"
    if profile not in project.policy.profiles or profile == "readonly":
        return f"rule profile {profile!r} can't take rules"
    now = project.policy.ruling(action, profile, key)
    if STRICTNESS[ruling] <= STRICTNESS[now]:
        return f"rule {ruling} for {action} {key or ''} doesn't tighten the current {now}".replace("  ", " ")
    return None


def raise_law(project: Project, law: Law) -> dict:
    cited = list(dict.fromkeys(law.evidence))
    return project.ledger.append("law.raised", "conductor", " ".join(law.text.split()),
                                 evidence=cited, rule=law.rule)
