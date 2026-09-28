"""One pulse: check on things, start queued work, ask the conductor, record what was found.

`parallax pulse` is one command. Scheduling it is left to Task Scheduler or cron.
Every pulse is recorded, including the ones that found nothing.
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import evidence
from . import mission as missions
from .agents.base import Conductor
from .core import Project, refuse_inside_task
from .inbox import record_proposal
from .ledger import file_lock

BUSY = ("running", "launched")


def spawn_run(project: Project, task_id: str) -> int:
    """Start `parallax run <task>` detached, output to .parallax/runs/<task>.log."""
    runs = project.state / "runs"
    runs.mkdir(exist_ok=True)
    log = open(runs / f"{task_id}.log", "ab")
    kwargs: dict = {"cwd": project.root, "stdin": subprocess.DEVNULL, "stdout": log, "stderr": subprocess.STDOUT}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    with log:
        # -u: unbuffered, so the log shows progress as it happens, and survives a killed process
        return subprocess.Popen([sys.executable, "-u", "-m", "parallax.cli", "run", task_id], **kwargs).pid


def snapshot(project: Project, reported: list[str] = (), limit: int = 50) -> str:
    """What the conductor sees. All of it is data."""
    clip = lambda s: " ".join(str(s).split())[:300]
    tasks = list(project.tasks().items())[-limit:]
    lines = ["already reported by this pulse's checks (don't repeat):"] + [f"- {clip(f)}" for f in reported]
    lines += ["", "tasks:"] + [f"- {tid} [{t['status']}] profile={t['profile']}: {clip(t['goal'])}" for tid, t in tasks]
    lines += ["", "inbox:"]
    for e in project.inbox():
        d = e["data"]
        what = d.get("action") or d.get("stage") or ""
        lines.append(f"- {e['id']} {e['kind']} task={d.get('task') or '-'} {what}: {clip(e['reason'])}")
    entries = project.ledger.entries()
    proposals = {e["id"]: e for e in entries if e["kind"] == "proposal.raised"}
    rejected = [(proposals[e["data"]["decision"]], e) for e in entries
                if e["kind"] == "decision.resolved" and e["data"].get("about") == "proposal.raised"
                and e["data"]["outcome"] == "rejected" and e["data"]["decision"] in proposals]
    lines += ["", "rejected proposals (don't propose again):"]
    lines += [f"- {clip(p['reason'])} (rejected: {clip(r['reason'])})" for p, r in rejected[-10:]]
    return "\n".join(lines)


def pulse(project: Project, conductor: Conductor | None = None,
          launch: Callable[[Project, str], int] = spawn_run, now: datetime | None = None) -> dict:
    refuse_inside_task(project.root)
    with file_lock(project.state / "pulse.lock"):  # overlapping pulses run one after the other
        return _pulse(project, conductor, launch, now or datetime.now(timezone.utc))


def _pulse(project: Project, conductor: Conductor | None, launch, now: datetime) -> dict:
    limits = project.policy.limits
    findings: list[str] = []

    # 1. checks: a busy task that's gone quiet, with nothing waiting on you, may have died
    waiting_on_you = {e["data"].get("task") for e in project.inbox()}
    cutoff = now - timedelta(minutes=limits["stale_minutes"])
    for tid, t in project.tasks().items():
        if t["status"] in BUSY and tid not in waiting_on_you and datetime.fromisoformat(t["last"]) < cutoff:
            why = f"no activity for {limits['stale_minutes']} minutes, the run may have died"
            project.ledger.append("stuck.raised", "parallax", why, task=tid)
            findings.append(f"task {tid} flagged stuck: {why}")

    # evidence: requests you keep approving become promotion proposals (code, not a model)
    for p in evidence.raise_promotions(project, now):
        findings.append(f"promotion proposed: {p['data']['action']} {p['data']['key']}")

    # 2. launch queued tasks, oldest first, up to the cap
    tasks = project.tasks()
    busy = sum(t["status"] in BUSY for t in tasks.values())
    queued = [tid for tid, t in tasks.items() if t["status"] == "queued"]
    launched = []
    for tid in queued[:max(limits["max_parallel"] - busy, 0)]:
        project.ledger.append("run.launched", "parallax", "", task=tid)  # before spawning: no double launch
        try:
            launch(project, tid)
            launched.append(tid)
        except OSError as err:
            findings.append(f"task {tid} failed to launch: {err}")

    # 3. the conductor reads the mission and proposes
    mission = missions.load(project.root)
    if mission is None:
        conducted = "skipped: no mission.md"
    elif conductor is None:
        conducted = "skipped: no conductor"
    else:
        conducted = "ran"
        try:
            report = conductor.review(mission.text, snapshot(project, findings))
        except Exception as err:  # recorded for you, never retried silently
            conducted = "failed"
            findings.append(f"conductor failed: {type(err).__name__}: {err}")
        else:
            findings += report.findings
            for p in report.proposals:
                record_proposal(project, p, source="pulse")
            pending = {e["id"] for e in project.inbox()}
            for r in report.recommendations:
                if r.item in pending and r.option in ("approve", "reject"):
                    project.ledger.append("recommendation.recorded", "conductor", r.why,
                                          item=r.item, option=r.option)

    # 4. record it, even when nothing was found
    counts = {status: sum(t["status"] == status for t in project.tasks().values())
              for status in ("queued", *BUSY, "ready", "disputed", "stuck")}
    counts["inbox"] = len(project.inbox())
    project.ledger.append("pulse.recorded", "parallax", "; ".join(findings) or "no finding",
                          findings=findings, launched=launched, counts=counts, conductor=conducted)
    return {"findings": findings, "launched": launched, "counts": counts, "conductor": conducted}
