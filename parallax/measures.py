"""M16: first pass, rework cycles and waits, per task and per split request, from the ledger only.

- First pass: a task that reached Ready on its own (the check passed: check.finished ready) with no
  rework cycle and no send-back (task.redraft) in any attempt.
- Rework cycles: Maker's rework.started entries, across every attempt. A task hit the cap when its
  check came to you after the last cycle the policy allowed (disagreement.raised with rework_cap; an
  entry from before that field says so in its reason).
- Waiting on you: the time a task sat on the board as Ready or Needs you, from the entry that put it
  there (a Decision needed, Ready, your stop) to your next entry for it (or code carrying out an
  answer you gave on another card), or until it moved on by itself. An answer that leaves it waiting
  (Ready after you overrule Second Eye) starts a new wait; one that leaves it as it was, until code
  moves it on (your launch, before the build starts), doesn't.
- Working without you: from task.created to the entry that finished it, less the waits.
stats.py sums them over finished tasks only: an open task's numbers aren't final.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from statistics import median

from . import status

WAITING = ("ready", "needs you")  # board columns that wait on you
MOVING = ("open", "plan approved")  # statuses code moves on from at once: the board shows them as needs you
ASKS = ("stuck.raised", "disagreement.raised", "review.requested")  # Decision needed
CAPPED = "the check still fails after "  # the reason, on a rework-cap entry from before rework_cap was recorded


@dataclass
class Measure:
    ready: bool = False  # the check passed on its own, at least once
    reworks: int = 0
    sent_back: bool = False
    capped: bool = False
    finished: bool = False
    start: float = 0.0
    end: float | None = None  # the latest entry that put it in done
    waits: list[tuple[float, float]] = field(default_factory=list)  # closed waits, (from, to) in seconds
    since: float | None = None  # an open wait's start

    @property
    def first_pass(self) -> bool:
        return self.ready and not self.reworks and not self.sent_back

    def closed(self) -> list[tuple[float, float]]:
        """The waits up to the finishing entry: what waited is summed over."""
        return [w for w in self.waits if self.end is not None and w[1] <= self.end]

    @property
    def waited(self) -> float:
        return sum(b - a for a, b in self.closed())

    @property
    def worked(self) -> float:
        return max(0.0, (self.end or self.start) - self.start - self.waited)


def _at(e: dict) -> float:
    return datetime.fromisoformat(e["ts"]).timestamp()


def _yours(e: dict) -> bool:
    """Your entry, or code carrying out your answer from another card (one trust for the same commands)."""
    return e["actor"] == "human" or (e["kind"] == "decision.resolved" and bool(e["data"].get("by")))


def measure(entries: list[dict]) -> dict[str, Measure]:
    """Every lifecycle task's measures, oldest first."""
    states: dict[str, dict] = {}
    out: dict[str, Measure] = {}
    for e in entries:
        kind, d, tid = e["kind"], e["data"], e["data"].get("task")
        before = (states.get(tid) or {}).get("status")
        status.step(states, e)
        if kind == "task.created":
            if d.get("intent"):
                out[tid] = Measure(start=_at(e))
            continue
        m = out.get(tid)
        if m is None:
            continue
        at = _at(e)
        if kind == "rework.started":
            m.reworks += 1
        elif kind == "task.redraft":
            m.sent_back = True
        elif kind == "check.finished" and d.get("status") == "ready":
            m.ready = True
        elif kind == "disagreement.raised" and (d.get("rework_cap") or e["reason"].startswith(CAPPED)):
            m.capped = True
        s = states[tid]["status"]
        where = status.board(s)
        waiting = where in WAITING and s not in MOVING
        if m.since is not None and (_yours(e) or not waiting):
            m.waits.append((m.since, at))
            m.since = None
        if waiting and m.since is None and (s != before or kind in ASKS):  # it just came to you
            m.since = at
        if where == "done" and not m.finished:
            m.end = at
        m.finished = where == "done"
    return out


def union(waits: list[tuple[float, float]]) -> float:
    """Seconds covered by any of the waits: a request waits on you while any of its parts does."""
    total, reach = 0.0, None
    for a, b in sorted(waits):
        if reach is None or a > reach:
            total += b - a
            reach = b
        elif b > reach:
            total += b - reach
            reach = b
    return total


def span(seconds: float | None) -> str:
    """A duration in a few characters: 40s, 12m, 2h 5m, 3d 4h."""
    if seconds is None:
        return "-"
    s = int(round(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


def summary(done: dict[str, Measure]) -> list[str]:
    """The finished tasks' lines: first pass, rework cycles, and the waits."""
    if not done:
        return []
    first = sum(m.first_pass for m in done.values())
    reworks = [m.reworks for m in done.values()]
    capped = sum(m.capped for m in done.values())
    longest = max(done, key=lambda t: done[t].waited)
    return [
        f"first pass {first} of {len(done)} finished ({first * 100 // len(done)}%): ready with no rework and no send-back.",
        f"rework cycles per finished task: median {median(reworks):g}, max {max(reworks)}. "
        f"{capped} hit the rework cap.",
        f"waiting on you: median {span(median(m.waited for m in done.values()))}, longest {span(done[longest].waited)} "
        f"({longest}). working without you: median {span(median(m.worked for m in done.values()))}.",
    ]


def request_line(parts: list[str | None], measures: dict[str, Measure]) -> tuple[str, int, str, str]:
    """A split request's first pass (of its finished parts), rework cycles, and, once every part is
    finished, its wait (any part waiting on you, counted once) and the rest of its time."""
    made = [measures[t] for t in parts if t in measures]
    done = [m for m in made if m.finished]
    first = f"{sum(m.first_pass for m in done)}/{len(done)}"
    reworks = sum(m.reworks for m in made)
    if len(done) < len(parts) or not done:
        return first, reworks, "-", "-"
    waited = union([w for m in done for w in m.closed()])
    whole = max(m.end for m in done) - min(m.start for m in done)
    return first, reworks, span(waited), span(max(0.0, whole - waited))
