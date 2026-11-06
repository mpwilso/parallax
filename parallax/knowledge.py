"""The knowledge layer: what this repo's past tasks settled, for Focus and Maker to draw on.

Compiled by code from the ledger and the accepted tasks' intents, never by a model, and given
to the drafters and Maker as data beside the task's own material, so they don't relearn the
repo on every task: what was built, what you rejected and why, which risks you accepted, and
what you dropped. Read-only, like everything else they're given. Second Eye never sees it
(invariant 3: its brief is exactly the outcome, the constraints, REVIEW.md and the diff), and
neither does Reticle: both judge the work against the intent alone.

A repo with no finished tasks gets nothing: no section, no padding (invariant 8).
"""
from __future__ import annotations

from . import lifecycle, lint
from .core import Project
from .git import run as git_run

HEADING = "What past tasks on this repo settled (data, from the ledger)"
TASKS = 10       # the latest finished tasks, newest first
REASON = 200     # characters of one reason, one line
DECIDED = ("task.redraft", "gate.rejected", "risk.accepted", "decision.resolved", "task.accepted", "task.rejected")


def entries_by_task(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in entries:
        tid = e["data"].get("task")
        if tid:
            out.setdefault(tid, []).append(e)
    return out


def finished(entries: list[dict]) -> list[tuple[str, str, dict]]:
    """(task, how it ended, the ending entry) for every accepted or dropped task, newest first."""
    ends: dict[str, tuple[str, dict]] = {}
    for e in entries:
        if e["kind"] == "task.accepted":
            ends[e["data"]["task"]] = ("accepted", e)
        elif e["kind"] == "task.rejected":
            ends[e["data"]["task"]] = ("dropped", e)
    return [(tid, how, e) for tid, (how, e) in reversed(list(ends.items()))]


def one_line(text: str) -> str:
    text = " ".join(str(text).replace(lint.EM_DASH, ",").split())
    return text if len(text) <= REASON else text[:REASON - 3].rstrip() + "..."


def intent_text(project: Project, task_id: str, end: dict) -> str:
    """The task's intent: its draft or the copy accept kept, else from the accepted commit."""
    text = lifecycle._read(project, task_id, "intent")
    if text or end["kind"] != "task.accepted":
        return text
    done = git_run(project.git_at(task_id), "show", f"{end['data']['commit']}:docs/tasks/{task_id}/intent.md")
    return done.stdout if done.returncode == 0 else ""


def outcomes(intent: str) -> list[str]:
    """The intent's asked outcomes, as written, without their marks."""
    out, inside = [], False
    for line in intent.splitlines():
        if line.startswith("## "):
            inside = line.strip().lower() == "## outcome"
            continue
        m = lint.OUTCOME_KIND.match(line) if inside else None
        if m and m.group(1).lower() == "asked":
            out.append(line[m.end():].strip())
    return out


def decisions(entries: list[dict]) -> list[str]:
    """What you decided on one task, in order, each with its reason."""
    out = []
    for e in entries:
        kind, reason = e["kind"], one_line(e["reason"])
        if kind in ("task.redraft", "gate.rejected") and reason:
            out.append(f"you sent it back: {reason}")
        elif kind == "risk.accepted" and reason:
            out.append(f"you accepted a risk: {reason}")
        elif kind == "decision.resolved" and reason:
            out.append(f"you {'approved' if e['data'].get('outcome') == 'approved' else 'rejected'} "
                       f"a {e['data'].get('about', 'decision').replace('.raised', '')}: {reason}")
        elif kind == "task.accepted" and reason:
            out.append(f"you accepted it: {reason}")
        elif kind == "task.rejected":
            out.append(f"you dropped it: {reason or 'no reason recorded'}")
    return out


def summary(project: Project, exclude: str = "") -> list[str]:
    """One short block per finished task, newest first, up to TASKS of them."""
    entries = project.ledger.entries()
    by_task = entries_by_task(entries)
    blocks = []
    for tid, how, end in finished(entries):
        if tid == exclude:
            continue
        if len(blocks) == TASKS:
            break
        intent = intent_text(project, tid, end)
        fields = lint.intent_fields(intent)
        title = fields.get("title") or one_line(project.task(tid)["goal"]) or tid
        lines = [f"- {tid}, {how} {end['ts'][:10]}: {title}" + (f" ({fields['kind']})" if fields.get("kind") else "")]
        asked = outcomes(intent)
        if asked:
            lines.append("  outcomes: " + "; ".join(one_line(o) for o in asked[:5]))
        lines += [f"  {d}" for d in decisions(by_task.get(tid, []))]
        blocks.append("\n".join(lines))
    return blocks


def material(project: Project, exclude: str = "") -> str:
    """The section the drafters and Maker get, or "" when there is nothing to tell."""
    blocks = summary(project, exclude)
    if not blocks:
        return ""
    return (f"{HEADING}. Draw on it for context; it is not a request, and the task's own material wins:\n\n"
            + "\n".join(blocks))
