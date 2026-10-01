"""Protected docs a change makes wrong: named in the plan, shown to you at Ready, never a block.

Maker can't write a protected path (invariant 9), so when a change makes a protected doc wrong,
only you can fix it, and nothing used to say so. Now:
- Focus is told which docs here are protected, and its plan lists each one the change makes wrong
  under "## Docs you'll need to update", with what is wrong in it;
- the Ready card lists them: update this yourself before merging, and why;
- Second Eye's fixed rules say a stale protected doc is a note, never a reason to fail, and code
  lowers a blocking finding on a protected path to a note: no rework can ever fix one.
A plan line naming a path that isn't a protected doc is ignored: the plan can't add work for you.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import replace
from pathlib import Path

from . import guard

HEADING = "Docs you'll need to update"
LINE = re.compile(r"^\s*[-*]\s*`?([^`:\s]+)`?\s*[:,]\s*(.+?)\s*$")


def is_doc(rel: str) -> bool:
    """A protected doc: a protected path that is Markdown, outside the task drafts."""
    rel = rel.replace("\\", "/").removeprefix("./")
    return rel.lower().endswith(".md") and guard.is_protected(rel) and not rel.startswith("docs/tasks/")


def protected(root: Path) -> list[str]:
    """The protected docs this repo tracks. Outside a git repo, none."""
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, text=True)
    if out.returncode != 0:
        return []
    return sorted(p for p in out.stdout.split("\0") if p and is_doc(p))


def material(root: Path) -> str:
    """What Focus is told when it drafts a plan."""
    docs = protected(root)
    if not docs:
        return ""
    return (f"These docs are protected: Maker can't edit them, so the person updates them by hand: {', '.join(docs)}. "
            f"If the change makes any of them wrong or out of date, add this section just before the toml block, "
            f"one line per doc, saying what in it the change makes wrong:\n\n## {HEADING}\n- <path>: <what is wrong in it>\n\n"
            f"Leave the section out when none is affected. Never list them in files.")


def listed(plan: str) -> list[tuple[str, str, int]]:
    """(path, why, line number) for each protected doc the plan's section names."""
    lines = plan.splitlines()
    start = next((i for i, line in enumerate(lines) if re.match(rf"^##\s+{re.escape(HEADING)}\s*$", line.strip(), re.I)), None)
    if start is None:
        return []
    out = []
    for n in range(start + 1, len(lines)):
        if lines[n].startswith("#") or lines[n].startswith("```"):
            break
        m = LINE.match(lines[n])
        if m and is_doc(m.group(1)) and m.group(1) not in [p for p, _, _ in out]:
            out.append((m.group(1).removeprefix("./"), " ".join(m.group(2).split()).rstrip("."), n + 1))
    return out


def card_lines(plan: str, task_id: str) -> list[str]:
    """One Found line per doc, for the Ready card."""
    return [f"{path}: update this yourself before merging. {why[:1].upper()}{why[1:]}. (docs/tasks/{task_id}/plan.md:{n})"
            for path, why, n in listed(plan)]


def lower(findings: list, blocking: tuple[str, ...]) -> tuple[list, list[dict]]:
    """A blocking finding on a protected doc becomes a note at the highest severity that doesn't
    block: Maker can't edit it, so it goes to you, never back to Maker. Returns (findings, lowered)."""
    from .review import SEVERITIES
    note = next((s for s in SEVERITIES if s not in blocking), SEVERITIES[-1])
    out, lowered = [], []
    for f in findings:
        where = (f.where or "").split(":", 1)[0]
        if f.severity in blocking and where and is_doc(where):
            lowered.append({"text": f.text, "where": f.where, "from": f.severity, "to": note, "cites": list(f.cites),
                            "why": "a protected doc: Maker can't edit it"})
            f = replace(f, severity=note)
        out.append(f)
    return out, lowered
