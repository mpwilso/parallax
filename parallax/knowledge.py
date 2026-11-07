"""The knowledge layer: what this repo's past tasks settled, for Focus and Maker to draw on.

Compiled by code from the ledger and the accepted tasks' intents, never by a model, and given
to the drafters and Maker as data beside the task's own material, so they don't relearn the
repo on every task: what was built, what you rejected and why, which risks you accepted, and
what you dropped. Read-only, like everything else they're given. Second Eye never sees it
(invariant 3: its brief is exactly the outcome, the constraints, REVIEW.md and the diff), and
neither does Reticle: both judge the work against the intent alone.

Only text a person approved or wrote goes in (invariant 10: a goal may be pasted issue text).
An accepted task's title and asked outcomes come from the intent in its accepted commit, the
file you approved, never from a draft made since. A dropped task gives its id, its date and your
reason, never its goal or its drafts. Every decision line is your own reason. A past task whose
intent can't be read is left out, so one bad file never stops a build.

A repo with no finished tasks gets nothing: no section, no padding (invariant 8).
"""
from __future__ import annotations

import re

from . import lint
from .core import Project
from .git import run as git_run

HEADING = "What past tasks on this repo settled (data, from the ledger)"
TASKS = 10       # the latest finished tasks, newest first
REASON = 200     # characters of one field or reason, one line
OUTCOMES = 5     # asked outcomes per task
COMMIT = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
DECIDED = ("task.redraft", "gate.rejected", "risk.accepted", "decision.resolved", "task.accepted", "task.rejected")


def entries_by_task(entries: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for e in entries:
        tid = e["data"].get("task")
        if tid:
            out.setdefault(tid, []).append(e)
    return out


def finished(entries: list[dict]) -> list[tuple[str, str, dict]]:
    """(task, how it ended, the ending entry) for every accepted or dropped task, newest ending first.
    A task that ended more than once counts as its latest ending, in that ending's place."""
    ends: dict[str, tuple[str, dict]] = {}
    for e in entries:
        how = {"task.accepted": "accepted", "task.rejected": "dropped"}.get(e["kind"])
        if how and (tid := e["data"].get("task")):
            ends.pop(tid, None)
            ends[tid] = (how, e)
    return [(tid, how, e) for tid, (how, e) in reversed(list(ends.items()))]


def one_line(text: str) -> str:
    text = " ".join(str(text).replace(lint.EM_DASH, ",").split())
    return text if len(text) <= REASON else text[:REASON - 3].rstrip() + "..."


def approved_intent(project: Project, task_id: str, commit: str, task: dict) -> str | None:
    """The intent you approved: docs/tasks/<id>/intent.md in the task's accepted commit, never a
    draft made since. "" for a task with no intent, with no git call. None when it can't be read
    (no such commit or file, bytes that aren't UTF-8, git failing), and the task is left out."""
    if not task.get("intent"):
        return ""
    if not COMMIT.fullmatch(commit):
        return None
    try:  # every accepted commit is in the root's object store, which the worktrees share
        done = git_run(project.root, "show", f"{commit}:docs/tasks/{task_id}/intent.md", text=False)
        return done.stdout.decode("utf-8") if done.returncode == 0 else None
    except (OSError, UnicodeDecodeError):
        return None


def _cache(project: Project) -> dict[tuple[str, str], str]:
    """Intents read so far, by (task, accepted commit): a commit's file never changes."""
    return vars(project).setdefault("_approved_intents", {})


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
    cache, tasks = _cache(project), None
    blocks = []
    for tid, how, end in finished(entries):
        if tid == exclude:
            continue
        if len(blocks) == TASKS:
            break
        when = f"- {tid}, {how} {end['ts'][:10]}"
        if how == "dropped":  # nobody approved its goal or its drafts: only your reason
            blocks.append(f"{when}\n  you dropped it: {one_line(end['reason']) or 'no reason recorded'}")
            continue
        key = (tid, str(end["data"].get("commit", "")))
        if key not in cache:
            tasks = project.tasks() if tasks is None else tasks  # derived once, and only for a task not read yet
            if (text := approved_intent(project, tid, key[1], tasks.get(tid, {}))) is None:
                continue
            cache[key] = text
        fields = lint.intent_fields(cache[key])
        title, kind = one_line(fields.get("title", "")), one_line(fields.get("kind", ""))
        lines = [when + (f": {title}" if title else "") + (f" ({kind})" if kind else "")]
        if asked := outcomes(cache[key]):
            lines.append("  outcomes: " + "; ".join(one_line(o) for o in asked[:OUTCOMES]))
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
