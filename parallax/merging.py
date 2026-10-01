"""What Accept and merge is doing while it works, from the ledger. A task is Merging from your click
until the base branch moves, or the tests fail, or a merge conflicts (accept.merge_now). The card
says which step it's on, how long it has run, and how long that usually takes."""
from __future__ import annotations

from datetime import datetime, timezone
from statistics import median

from .core import Project


def _ts(ts: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def usual_seconds(project: Project) -> int | None:
    """How long the pre-merge test run usually takes here: the median of the last five that ran."""
    started: dict[str, datetime] = {}
    took: list[float] = []
    for e in project.ledger.entries():
        if e["kind"] == "merge.started":
            when = _ts(e["ts"])
            if when:
                started[e["data"].get("task")] = when
        elif e["kind"] == "merge.tested" and e["data"].get("command"):
            if e["data"].get("seconds") is not None:
                took.append(float(e["data"]["seconds"]))
            elif e["data"].get("task") in started and (end := _ts(e["ts"])):
                took.append((end - started[e["data"]["task"]]).total_seconds())
    took = [t for t in took if t >= 0][-5:]
    return round(median(took)) if took else None


def shown(seconds: float | None) -> str:
    if seconds is None:
        return ""
    s = int(seconds)
    return f"{s // 60}m {s % 60:02d}s" if s >= 60 else f"{s}s"


def info(project: Project, task_id: str, now: datetime | None = None) -> dict | None:
    """{text, started, usual_seconds, elapsed_seconds, line} while the task is merging, else None."""
    t = project.task(task_id)
    if t["status"] != "merging":
        return None
    mine = [e for e in project.ledger.entries() if e["data"].get("task") == task_id]
    began = next((e for e in reversed(mine) if e["kind"] == "merge.started"), None)
    clicked = next((e for e in reversed(mine) if e["kind"] == "task.accepted"), None)
    d = (began or clicked or {}).get("data", {})
    target = d.get("target") or "the base branch"
    tests = bool(project.policy.merge["test_command"].strip())
    if began and not d.get("ff", True):
        text = f"Merging {target} in, then running the tests" if tests else f"Merging {target} in, then landing it"
    elif tests:
        text = "Merging: running the tests on the commit it would land"
    else:
        text = f"Merging: landing it on {target}"
    since = (began or clicked or {}).get("ts", "")
    elapsed = ((now or datetime.now(timezone.utc)) - _ts(since)).total_seconds() if _ts(since) else None
    usual = usual_seconds(project) if tests else None
    line = text + (f". {shown(elapsed)} so far" if elapsed is not None else "") + \
        (f"; it usually takes about {shown(usual)}." if usual else ".")
    return {"text": text, "started": since, "usual_seconds": usual, "elapsed_seconds": elapsed, "line": line}
