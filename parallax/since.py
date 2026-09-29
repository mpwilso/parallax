"""What changed since the version you rejected: the first thing a redrafted card says.

A reject at Ready starts a new attempt. When that attempt comes back, its card leads with your
reason, which parts of the intent and plan changed and which didn't, and how the new change
differs from the rejected one, file by file. The rejected intent and plan are kept at the reject;
the rejected change is its last reviewed tree, still in git.
"""
from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path

from . import lifecycle, lint, sandbox, tree
from .core import Project

DOCS = ("intent", "spec", "plan")


def _kept(project: Project, task_id: str, n: int) -> Path:
    return sandbox.task_home(project.root, task_id) / "rejected" / str(n)


def keep(project: Project, task_id: str, n: int) -> None:
    """At a reject: keep the version you rejected, before the drafters replace it."""
    dest = _kept(project, task_id, n)
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    for doc in DOCS:
        path = lifecycle.found_doc(project, task_id, doc)
        if path:
            shutil.copyfile(path, dest / f"{doc}.md")


def _parts(text: str) -> dict[str, str]:
    """A drafted file by part: its header fields, each ## section, and the plan's toml block."""
    body, _, toml = text.partition("```toml")
    parts: dict[str, str] = {}
    name = "header"
    for line in body.splitlines():
        m = re.match(r"^#+\s+(.*\S)\s*$", line)
        if m:
            name = m.group(1)
            continue
        parts[name] = parts.get(name, "") + line.strip() + "\n"
    if toml:
        parts["toml block"] = toml
    return {k: v.strip() for k, v in parts.items()}


def _doc_line(doc: str, old: str, new: str) -> str:
    if old.strip() == new.strip():
        return f"{doc}: unchanged"
    a, b = _parts(old), _parts(new)
    changed = [k for k in dict.fromkeys([*a, *b]) if a.get(k) != b.get(k)]
    same = [k for k in a if k in b and a[k] == b[k]]
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=0))
    plus = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    minus = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    line = f"{doc}: {', '.join(changed)} changed (+{plus} -{minus} lines)"
    if same:
        line += f"; {', '.join(same)} the same"
    return line


def _plan_files(text: str) -> list[str]:
    data, _, why = lint.plan_block(text)
    return list(data["files"]) if data and not why else []


def lines(project: Project, task_id: str, entries: list[dict]) -> list[str]:
    """The card's lead after a reject, or [] when this attempt didn't start from one.

    entries: the task's own ledger entries, oldest first."""
    starts = [i for i, e in enumerate(entries) if e["kind"] == "task.redraft"]
    if not starts:
        return []
    reject = entries[starts[-1]]
    n = len(starts)
    out = [f"you rejected the last version: \"{' '.join(reject['reason'].split())}\" (ledger {reject['id']})"]
    kept = _kept(project, task_id, n)
    for doc in DOCS:
        old = kept / f"{doc}.md"
        new = lifecycle.found_doc(project, task_id, doc)
        if not old.exists() and not new:
            continue
        cite = f"docs/tasks/{task_id}/{doc}.md:1"
        if not new:
            out.append(f"{doc}: dropped in the redraft (ledger {reject['id']})")
        elif not old.exists():
            out.append(f"{doc}: new in the redraft ({cite})")
        else:
            was, now = old.read_text(encoding="utf-8"), new.read_text(encoding="utf-8")
            line = _doc_line(doc, was, now)
            if doc == "plan":
                added = [f for f in _plan_files(now) if f not in _plan_files(was)]
                gone = [f for f in _plan_files(was) if f not in _plan_files(now)]
                line += f"; files added: {', '.join(added)}" if added else ""
                line += f"; files dropped: {', '.join(gone)}" if gone else ""
            out.append(f"{line} ({cite})")
    before = [e for e in entries[:starts[-1]] if e["kind"] == "check.staged"]
    after = [e for e in entries[starts[-1]:] if e["kind"] == "check.staged"]
    if before and after:
        old, new = before[-1]["data"], after[-1]["data"]
        wt = Path(project.task(task_id)["worktree"])
        try:
            differ = set(tree.changed_between(wt, old["tree"], new["tree"]))
        except Exception:  # the old tree is gone from git: say so rather than guess
            out.append(f"the change: can't compare with the rejected version (ledger {after[-1]['id']})")
            return out
        files = list(dict.fromkeys([*old["files"], *new["files"]]))
        moved = [f for f in files if f in differ]
        kept_same = [f for f in files if f not in differ]
        line = "the change: " + (f"differs from the rejected one in {', '.join(moved)}" if moved else "same as the rejected one")
        if moved and kept_same:
            line += f"; the same in {', '.join(kept_same)}"
        out.append(f"{line} (ledger {after[-1]['id']})")
    return out
