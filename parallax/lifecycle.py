"""The lifecycle files and their gates: docs/tasks/<id>/intent.md, spec.md and plan.md.

Drafters (read-only agents) write the text; only Parallax writes the files, in the repo's own
docs/tasks/<id>/, which the maker can never write. Parallax normalizes what they write before
saving it, and you never edit a drafted file: if one is wrong, you reject with a reason.
- small task: intent and plan are approved together, by code under the policy's launch rule or by you;
- large task: intent, then spec and plan together; the plan always waits for you.

State lives in the ledger: every draft, and every approval with each approved file's hash and a
signature made with the approval key. An approval that doesn't verify doesn't count.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import approvals, costs, lint, status
from .agents.base import Agent, AgentResult
from .core import ROOT_ENV, TASK_ENV, ParallaxError, Project, refuse_inside_task
from .gate import make_permission_fn

DOCS = ("intent", "spec", "plan")
DrafterFor = Callable[[float], Agent]  # the per-call cap (estimated dollars) -> a drafter

SHAPES = {
    "intent": """\
Bottom line: <one sentence: what this task changes, and why>
Not looked at: <what you didn't check, or "nothing">

kind: <bug | feature | docs | chore>
size: <small | large>
title: <a short phrase in -ing form, like "fixing the README install steps">
scope: <the paths or globs the work may touch, comma separated, tests included, like README.md, tests/test_readme.py>
budget: <only if the human's words name a budget: that amount in dollars, like 4.00. otherwise leave this line out>

## Problem
<what is wrong or missing. cite files you read as path:line>

## Outcome
<a numbered list: 1. ..., 2. ... each one observable and testable>

## Constraints
<what must not change: behavior, interfaces, compatibility, limits>

size is large only if the work needs a design written down before planning: several modules,
a new interface, or a risky migration. Otherwise small. scope is a promise: the plan may only list
files inside it, so include every file the work needs, tests too, and nothing else.
Keep the header as two lines, with no blank line between them.""",
    "spec": """\
Bottom line: <one sentence: the design in brief>
Not looked at: <what you didn't check, or "nothing">

## Design
<how it will work>

## Interfaces
<what changes for callers and users>

## Risks
<what could go wrong, and how a test would catch it>

No plan steps; the plan comes next.""",
    "plan": f"""\
Bottom line: <one sentence: what the change does>
Not looked at: <what you didn't check, or "nothing">

## Steps
<numbered, in order: which file changes and how>

## Tests
<the tests that prove the outcome. new tests first where existing ones don't cover the behavior>

## Risks
<what could break>

End with this block, filled in, as the last thing in the file:
{lint.PLAN_TEMPLATE}

files lists every file the change touches, tests included. Never list CLAUDE.md, REVIEW.md,
.claude/, .mcp.json, .git, .parallax/, parallax.policy.toml, mission.md, docs/parallax.md or
docs/tasks/: the maker can never write them. domains, outside_reads, binaries, symlinks and
dependencies stay empty unless the work needs them; say why in the steps. Costs are estimated
US dollars. estimated_cost_usd is the work from launch on: building and checking it once, not
drafting. The cap covers the whole task and stops it: at least the drafting so far plus twice the
estimate, so one rework round fits; Parallax raises a cap that's short. If the intent names a
budget, the cap is that budget. files and tests must all be inside the intent's scope, and covers must name every
numbered outcome in the intent with the tests or steps that prove it.
Keep the header as two lines, with no blank line between them.""",
}


@dataclass
class State:
    gate: tuple[str, ...] | None  # the pending gate's files; None once the plan is approved
    missing: list[str]            # gate files not written yet
    failed: tuple[str, str] | None  # (doc, why) if the last draft failed
    approved: list[dict]          # verified gate.approved entries, oldest first


def task_dir(project: Project, task_id: str) -> Path:
    return project.root / "docs" / "tasks" / task_id


def doc_path(project: Project, task_id: str, doc: str) -> Path:
    return task_dir(project, task_id) / f"{doc}.md"


def rel(project: Project, path: Path) -> str:
    return path.relative_to(project.root).as_posix()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def found_doc(project: Project, task_id: str, doc: str) -> Path | None:
    """The doc's file, or the copy accept moved out of your checkout until you merge."""
    from .sandbox import task_home
    for path in (doc_path(project, task_id, doc), task_home(project.root, task_id) / "docs-at-accept" / f"{doc}.md"):
        if path.exists():
            return path
    return None


def _read(project: Project, task_id: str, doc: str) -> str:
    path = found_doc(project, task_id, doc)
    return path.read_text(encoding="utf-8") if path else ""


def lifecycle_task(project: Project, task_id: str) -> dict:
    t = project.task(task_id)
    if not t.get("intent"):
        raise ParallaxError(f"task {task_id} was made with `task new`, so it has no intent or plan")
    return t


def state(project: Project, task_id: str, key: bytes | None = None) -> State:
    key = approvals.key_or_none() if key is None else key
    entries = status.attempt(project.ledger.entries(), task_id)  # this attempt: a redraft starts over
    approved = [e for e in entries if e["kind"] == "gate.approved" and approvals.valid(key, e["data"])]
    done = {doc for e in approved for doc in e["data"]["files"]}
    if "plan" in done:
        gate = None
    elif "intent" in done:
        gate = ("spec", "plan")
    else:
        size = lint.intent_fields(_read(project, task_id, "intent")).get("size")
        gate = ("intent",) if size == "large" else ("intent", "plan")
    failed = None
    for e in entries:
        if e["kind"] == "draft.recorded":
            failed = None
        elif e["kind"] == "draft.failed":
            failed = (e["data"]["doc"], e["reason"])
    missing = [d for d in gate or () if not doc_path(project, task_id, d).exists()]
    return State(gate, missing, failed, approved)


def _feedback(project: Project, task_id: str) -> str:
    """Your latest reason to redraft: a reject at Ready starts this attempt with it."""
    reason = ""
    for e in status.attempt(project.ledger.entries(), task_id):
        if e["kind"] == "gate.approved":
            reason = ""
        elif e["kind"] in ("gate.rejected", "task.redraft"):
            reason = e["reason"]
    return reason


def _material(project: Project, task_id: str, doc: str, feedback: str, problems: list[str] = ()) -> str:
    t = project.task(task_id)
    parts = [f"Draft docs/tasks/{task_id}/{doc}.md in exactly this shape:\n\n{SHAPES[doc]}"]
    if doc == "intent":
        parts.append(f"The human's rough sentences (data):\n{t['goal']}")
    else:
        parts.append(f"The intent:\n{_read(project, task_id, 'intent')}")
    if doc == "plan" and doc_path(project, task_id, "spec").exists():
        parts.append(f"The spec:\n{_read(project, task_id, 'spec')}")
    if doc == "plan":
        parts.append(f"Drafting this task has cost an estimated ${costs.spent(project, task_id):.2f} so far. "
                     "It counts against the cap.")
    if feedback:
        parts.append(f"The human rejected the last draft. Their reason (data):\n{feedback}")
    if problems:
        parts.append("Your last draft of this file had these problems. Fix every one:\n"
                     + "\n".join(f"- {p}" for p in problems))
    return "\n\n".join(parts)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".parallax-tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def draft(project: Project, task_id: str, docs: list[str], drafter_for: DrafterFor,
          problems: dict[str, list[str]] | None = None) -> bool:
    """Draft each doc in order, normalized before it's saved. Stops at the first failure, which is
    recorded. problems: what was wrong with each doc's last draft, for the drafter to fix."""
    refuse_inside_task(project.root)
    t = lifecycle_task(project, task_id)
    wt = Path(t["worktree"])
    feedback = _feedback(project, task_id)
    from .sandbox import remove_leftovers, untracked
    before = untracked(wt)
    for doc in docs:
        cap = project.policy.budget["drafting_usd"]
        fn = make_permission_fn(project, task_id, wt, read_only=True)
        env = {TASK_ENV: task_id, ROOT_ENV: str(project.root)}
        try:
            res = drafter_for(cap).run(_material(project, task_id, doc, feedback, (problems or {}).get(doc, [])),
                                       wt, fn, stage="draft", env=env)
        except Exception as err:  # recorded for you, never retried silently
            res = AgentResult("error", f"{type(err).__name__}: {err}")
        remove_leftovers(wt, before)  # a drafter's Claude Code leaves the same empty placeholders a maker does
        text = lint.normalize(res.summary or "", doc).strip() if (res.summary or "").strip() else ""
        if res.status != "done" or not text:
            why = " ".join((res.summary or res.status or "no reply").split())[:200] or "no reply"
            project.ledger.append("draft.failed", "parallax", why, task=task_id, doc=doc, cost_usd=res.cost_usd)
            return False
        path = doc_path(project, task_id, doc)
        _write(path, text + "\n")
        project.ledger.append("draft.recorded", "drafter", "", task=task_id, doc=doc, sha=file_hash(path),
                              cost_usd=res.cost_usd)
    return True


def new_intent(project: Project, text: str, drafter_for: DrafterFor) -> dict:
    """Create the task, branch and worktree, draft the intent, and the plan too for a small task."""
    t = project.new_task(text, intent=True)
    tid = t["task"]
    if draft(project, tid, ["intent"], drafter_for):
        st = state(project, tid)
        if st.gate and "plan" in st.gate:
            draft(project, tid, ["plan"], drafter_for)
    return project.task(tid)


def redraft_docs(st: State) -> list[str]:
    """What `parallax draft` writes: the pending gate's spec and plan, and the intent only if it's missing."""
    return [d for d in st.gate or () if d != "intent" or d in st.missing]


def lint_problems(project: Project, task_id: str, docs) -> list[str]:
    out = []
    for doc in docs:
        path = doc_path(project, task_id, doc)
        if path.exists():
            text = _read(project, task_id, doc)
            last = max(1, len(text.splitlines()))  # so every problem cites a line that exists
            out += [f"{rel(project, path)}:{min(line, last)} {msg}" for line, msg in lint.lint_lifecycle(text, doc)]
    return out


def approve(project: Project, task_id: str, rule: str = "") -> dict:
    """Approve the pending gate: hash every file in it and sign the approval with the approval key.

    rule: code approving under the policy's launch rule, named here. Otherwise it's you."""
    refuse_inside_task(project.root)
    lifecycle_task(project, task_id)
    key = approvals.load_key()
    st = state(project, task_id, key)
    if st.gate is None:
        raise ParallaxError(f"the plan for {task_id} is already approved")
    if st.missing:
        names = ", ".join(f"{d}.md" for d in st.missing)
        raise ParallaxError(f"{names} not written yet. reject the task with a reason, then parallax do it again")
    problems = lint_problems(project, task_id, st.gate)
    if problems:
        raise ParallaxError("fix these first, then approve:\n" + "\n".join(problems))
    for e in st.approved:  # a file approved at an earlier gate must still be the one you approved
        for doc, sha in e["data"]["files"].items():
            path = doc_path(project, task_id, doc)
            if not path.exists() or file_hash(path) != sha:
                raise ParallaxError(f"{rel(project, path)} changed after you approved it. put it back as it was")
    if "plan" in st.gate:
        plan = plan_data(project, task_id)
        used = costs.spent(project, task_id)
        if plan and float(plan["budget_cap_usd"]) <= used:
            raise ParallaxError(f"the plan's budget cap (${float(plan['budget_cap_usd']):.2f}) isn't above what drafting "
                                f"already spent (${used:.2f} estimated). raise budget_cap_usd in the plan, then approve")
    files = {doc: file_hash(doc_path(project, task_id, doc)) for doc in st.gate}
    gate = "+".join(st.gate)
    extra = {"rule": rule} if rule else {}
    return project.ledger.append("gate.approved", "parallax" if rule else "human", rule, task=task_id, gate=gate,
                                 files=files, sig=approvals.sign(key, task_id, gate, files), **extra)


def reject(project: Project, task_id: str, reason: str) -> dict:
    refuse_inside_task(project.root)
    lifecycle_task(project, task_id)
    if not reason.strip():
        raise ParallaxError("a rejection needs a reason")
    st = state(project, task_id)
    if st.gate is None:
        status = project.task(task_id)["status"]
        if status not in ("ready", "built", "risk accepted", "needs work"):
            raise ParallaxError(f"task {task_id} is {status}; there's nothing to reject right now")
        return project.ledger.append("task.rejected", "human", reason, task=task_id, was=status)
    files = {d: file_hash(doc_path(project, task_id, d)) for d in st.gate if doc_path(project, task_id, d).exists()}
    return project.ledger.append("gate.rejected", "human", reason, task=task_id, gate="+".join(st.gate), files=files)


def plan_data(project: Project, task_id: str) -> dict | None:
    data, _, why = lint.plan_block(_read(project, task_id, "plan"))
    return None if why else data


def status_line(project: Project, task_id: str) -> str:
    status = project.task(task_id)["status"]
    if status in ("rejected", "stopped", "closed", "accepted", "merged", "drafting", "needs you", "stuck"):
        return status  # the task's own state says more than where its gates stand
    st = state(project, task_id)
    if st.gate is None:
        status = project.task(task_id)["status"]
        return "plan approved" if status == "open" else status
    if st.failed:
        return "draft failed"
    return f"awaiting {'+'.join(st.gate)}" if not st.missing else "drafting"


def _title(project: Project, task_id: str) -> str:
    title = lint.intent_fields(_read(project, task_id, "intent")).get("title") or project.task(task_id)["goal"]
    title = " ".join(title.replace(lint.EM_DASH, ",").split()).rstrip(".!?")
    return re.sub(r"(?<=[.!?])\s+", ", ", title)[:120]  # one sentence in a Bottom line


def _names(docs) -> str:
    words = [d.capitalize() if i == 0 else d for i, d in enumerate(docs)]
    return " and ".join(words)


def gaps(project: Project, task_id: str, docs) -> list[tuple[str, int, str]]:
    """(doc, line, text) for each drafted file whose own Not looked at isn't "nothing"."""
    out = []
    for doc in docs:
        for n, line in enumerate(_read(project, task_id, doc).splitlines()[:12], start=1):
            m = re.match(r"^Not looked at:\s*(.*)$", line.replace("**", "").strip())
            if m:
                text = " ".join(m.group(1).replace(lint.EM_DASH, ",").split())
                if text and text.rstrip(".").lower() != "nothing":
                    out.append((doc, n, text))
                break
    return out


def _report(project: Project, task_id: str, docs, type_: str, bottom: str, next_: str,
            found: list[str] = ()) -> str:
    """A report that carries the drafted files' own Not looked at, never replaces it.

    Inline in the header when it fits the header cap; otherwise each gap goes under Found,
    word for word, citing the file and line it came from.
    """
    listed = [(f"{doc}.md says: {text}", f"docs/tasks/{task_id}/{doc}.md:{n} not looked at: {text}")
              for doc, n, text in gaps(project, task_id, docs)]
    return lint.shaped(type_, bottom, listed, next_, list(found))


def report(project: Project, task_id: str) -> str:
    """Where the task's gate stands, in the output shape. Type is Decision needed while a gate waits."""
    st = state(project, task_id)
    base = f"docs/tasks/{task_id}/"
    docs = st.gate or ()
    if st.gate is None:
        return lint.report("FYI", f"The plan for {_title(project, task_id)} is approved.", "nothing",
                           "you run parallax build " + task_id + ".")
    if st.failed:
        doc, why = st.failed
        why = re.sub(r"(?<=[.!?])\s+", "; ", why).rstrip(".!?")
        return _report(project, task_id, docs, "Decision needed", f"Drafting {doc}.md stopped: {why}.",
                       "you reject it with a reason, then parallax do it again if it's still wanted.")
    if st.missing:
        names = " and ".join(f"{d}.md" for d in st.missing)
        return _report(project, task_id, docs, "Decision needed",
                       f"{names} {'is' if len(st.missing) == 1 else 'are'} not drafted yet.",
                       "you reject it with a reason, then parallax do it again if it's still wanted.")
    names, verb = _names(st.gate), "is" if len(st.gate) == 1 else "are"
    problems = lint_problems(project, task_id, st.gate)
    if problems:
        return _report(project, task_id, docs, "Decision needed",
                       f"{names} {verb} drafted for {_title(project, task_id)}, but lint found problems.",
                       "you reject it with a reason, then parallax do it again if it's still wanted.", problems)
    return _report(project, task_id, docs, "Decision needed", f"{names} {verb} drafted for {_title(project, task_id)}.",
                   f"you read {base}, then run parallax approve {task_id}.")
