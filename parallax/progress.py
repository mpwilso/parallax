"""Where each task stands, for the UI's rows, strip and overview. From the ledger, no model.

The stage strip is the same five stages on every row and card: Focus, Reticle, Maker, Check,
Ready, each done, working, failed, or skipped (it didn't run). A row adds a status chip, spend
against the cap, elapsed time and one summary sentence; a done row, its outcome, date, cost and
touches. The overview is what the page shows when no card is open.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from . import costs, lifecycle, live, stats, status
from .core import Project

STAGES = (("focus", "Focus"), ("reticle", "Reticle"), ("maker", "Maker"), ("check", "Check"), ("ready", "Ready"))
WORKING = ("drafting", "building", "checking", "merging")
# a decision's kind -> the stage that stopped. cap, error and stuck stop whichever stage was running
STOPPED_AT = {"drafting": "focus", "budget": "focus", "turns": "maker", "guard": "maker", "scope": "check", "conflict": "check",
              "flows": "check", "rework": "check", "checker": "check", "tests": "check"}
FAILED_KINDS = {"cap", "rework", "checker", "tests", "turns", "error", "stuck", "drafting"}  # the work failed: chip Failed
SUMMARY = 110  # characters a row's sentence gets before it's shortened, at a word


def _running(kinds: list[str]) -> str:
    """The stage that was last under way, from what the ledger says started."""
    at = "focus"
    for k in kinds:
        if k == "gate.approved":  # approved: the build is next, so a stop after this is past Focus
            at = "maker"
        elif k == "reticle.started":
            at = "reticle"
        elif k == "maker.started":
            at = "maker"
        elif k in ("check.started", "check.staged", "tests.recorded", "flows.recorded"):
            at = "check"
    return at


def strip(project: Project, task_id: str, dec=None) -> list[dict]:
    """[{stage, name, state, agent}], in order. agent: the portrait to show when that stage is working."""
    t = project.task(task_id)
    entries = status.attempt(project.ledger.entries(), task_id)
    kinds = [e["kind"] for e in entries]
    board = status.board(t["status"])
    what = live.doing(entries)[0] if board in WORKING else ""
    agent = live.agent_of(what)
    working = {"focus": "focus", "reticle": "reticle", "maker": "maker", "second_eye": "check", "field": "check"}.get(agent or "")
    if board in WORKING and not working and what.startswith(("running", "checking", "preparing the build")):
        working = "check" if what.startswith(("running", "checking")) else None
    failed = None
    if dec is not None and dec.item is not None:
        failed = STOPPED_AT.get(dec.kind) or _running(kinds)
    last_check = next((e for e in reversed(entries) if e["kind"] == "check.finished"), None)
    done = {
        "focus": "gate.approved" in kinds or (dec is not None and dec.kind in ("review", "launch")),
        "reticle": "reticle.recorded" in kinds,
        "maker": any(e["kind"] == "build.finished" and e["data"].get("status") == "built" for e in entries),
        "check": bool(last_check and last_check["data"].get("status") == "ready"),
        "ready": t["status"] in ("ready", "accepted", "merged"),
    }
    out = []
    merging = t["status"] == "merging"
    for key, name in STAGES:
        if key == "ready" and merging:  # Accept and merge is running: the last stage says so
            out.append({"stage": key, "name": "Merging", "state": "working", "agent": None})
            continue
        if key == working:
            state = "working"
        elif key == failed or (key == "reticle" and "reticle.failed" in kinds):
            state = "failed"
        elif done[key]:
            state = "done"
        else:
            state = "skipped"
        portrait = (agent if key == "check" and agent in ("second_eye", "field") else "second_eye") if key == "check" \
            else (None if key == "ready" else key)
        out.append({"stage": key, "name": name, "state": state, "agent": portrait})
    return out


def chip(state: str, kind: str | None) -> str:
    """Working, Ready, Needs you or Failed."""
    if state == "merging":
        return "Merging"
    if state in WORKING:
        return "Working"
    if state == "ready" or kind == "ready":
        return "Ready"
    return "Failed" if kind in FAILED_KINDS else "Needs you"


def spend(project: Project, task_id: str) -> dict:
    """What it has spent, and its cap once a plan sets one."""
    plan = lifecycle.plan_data(project, task_id)
    cap = costs.budget(project, task_id, plan)[0] if plan else None
    return {"spent": round(costs.spent(project, task_id), 2), "cap": round(cap, 2) if cap else None}


def sentence(text: str, limit: int = SUMMARY) -> str:
    """One complete sentence. If it's too long, cut at the last word that fits and end with an
    ellipsis, never mid-word and never after a dangling comma or colon."""
    text = " ".join(text.split())
    if text and text[-1] not in ".!?…":
        text += "."
    if len(text) <= limit:
        return text[:1].upper() + text[1:]
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(",;:-(")
    return cut[:1].upper() + cut[1:] + "…"


def started(project: Project, task_id: str) -> str:
    """When the task began: its first ledger entry."""
    first = next((e for e in project.ledger.entries() if e["data"].get("task") == task_id), None)
    return first["ts"] if first else ""


OUTCOMES = {"accepted": "Accepted, waiting for your merge", "merged": "Merged", "rejected": "Dropped",
            "stopped": "Stopped", "closed": "Closed"}


def done_row(project: Project, task_id: str, touches: dict[str, int]) -> dict:
    t = project.task(task_id)
    return {"outcome": OUTCOMES.get(t["status"], t["status"].capitalize()), "date": (t.get("last") or "")[:10],
            "cost_usd": round(t.get("cost_usd") or 0, 2), "touches": touches.get(task_id, 0)}


def _when(ts: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def overview(project: Project, now: datetime | None = None) -> list[str]:
    """The page when no card is open: plain sentences, no charts."""
    from .evals import staleness
    now = now or datetime.now(timezone.utc)
    tasks = project.tasks()
    counts = stats.touches(project)
    done = {tid: n for tid, n in counts.items() if status.board(tasks[tid]["status"]) == "done"}
    week = now - timedelta(days=7)
    spent = sum(e["data"].get("cost_usd") or 0 for e in project.ledger.entries()
                if (w := _when(e["ts"])) and w >= week)
    asked = sum(e["data"].get("ask_cost_usd") or 0 for e in project.ledger.entries()
                if e["kind"] == "ask.answered" and (w := _when(e["ts"])) and w >= week)
    lines = []
    if done:
        average = sum(done.values()) / len(done)
        more = sum(n > stats.TARGET for n in done.values())
        if len(done) == 1:
            n = next(iter(done.values()))
            lines.append(f"1 task finished. It needed you {n} time{'' if n == 1 else 's'}; the goal is once.")
        else:
            lines.append(f"{len(done)} tasks finished. They needed you {average:.1f} times each on average; the goal is once"
                         + (f", and {more} took more." if more else ", and every one met it."))
    else:
        lines.append("No task has finished yet. The goal is that each one needs you once: to accept it.")
    lines.append(f"${spent:.2f} spent on agents in the last 7 days" + (f", plus ${asked:.2f} on questions you asked." if asked else "."))
    evals = staleness(project)
    if evals:
        lines.append(evals[:1].upper() + evals[1:])
    recent = sorted((tid for tid in done), key=lambda tid: tasks[tid].get("last") or "", reverse=True)[:5]
    from .inbox import title
    for tid in recent:
        row = done_row(project, tid, counts)
        n = row["touches"]
        lines.append(f"{title(project, tid)}: {row['outcome'].lower()} on {row['date']}, ${row['cost_usd']:.2f}, "
                     f"{n} touch{'' if n == 1 else 'es'}.")
    return lines
