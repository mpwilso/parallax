"""REVIEW.md and the blind checker's brief.

REVIEW.md is one repo-level file: the review passes, the severity levels, and which of them block
Ready. You own it; the maker can't write it. A task's plan may add a tightening for that task's
check only. It's appended to the checker's copy of REVIEW.md and can't change which severities
block, so it can only add scrutiny, never remove it.

The checker's brief is exactly the brief's list: the intent's outcome and constraints, REVIEW.md
(with the task's tightening), and `git diff --cached --binary` of the reviewed tree without
docs/tasks/. Nothing else: no problem narrative, no plan, no spec, no maker notes.
"""
from __future__ import annotations

import re
from pathlib import Path

REVIEW_FILE = "REVIEW.md"
# TEMPLATE below is the general REVIEW.md: passes that apply to any code. It ships with Parallax,
# `parallax init` installs it where a repo has none, and evals use it. Parallax's own REVIEW.md is
# this template plus one pass for Parallax's own rules, without the housekeeping note, which came
# after it (tests/test_docs.py pins that).
SEVERITIES = ("blocker", "major", "minor", "nit")
DEFAULT_BLOCKING = ("blocker", "major")

TEMPLATE = """\
# Review

How Second Eye, the blind checker, reviews a change. You own this file; agents can't write it.

## Passes

1. Does the change achieve the outcome, within the constraints?
2. Correctness: logic errors, edge cases, error handling, off-by-one, wrong defaults.
3. Tests: do they test the new behavior, and would they fail without the change?
4. Safety: secrets, injection, unsafe file or shell handling, files that run automatically.
5. Scope: anything the outcome didn't ask for.

## Severities

- blocker: wrong, unsafe, or breaks something that worked.
- major: likely wrong in a case that matters, or a missing test for new behavior.
- minor: works, but should be better.
- nit: style or wording.

Housekeeping, such as a changelog entry, docs or a version number left out or not updated, is a note:
minor at most, never blocking, unless a pass above asks for it. It isn't behavior.

Blocking: blocker, major
"""


def load(root: Path) -> str:
    path = Path(root) / REVIEW_FILE
    return path.read_text(encoding="utf-8") if path.exists() else TEMPLATE


def blocking(text: str) -> tuple[str, ...]:
    """The severities that block Ready, from a 'Blocking:' line. The default if there's none."""
    m = re.search(r"^Blocking:\s*(.+)$", text, re.M | re.I)
    if not m:
        return DEFAULT_BLOCKING
    found = tuple(s.strip().lower() for s in m.group(1).split(",") if s.strip().lower() in SEVERITIES)
    return found or DEFAULT_BLOCKING


UNASKED = "cites no outcome you asked for"  # why code lowered it, as the card says


def enforce(findings: list, intent: str, blocking: tuple[str, ...]) -> tuple[list, list[dict]]:
    """Second Eye's asked-only rule, by code: a blocking finding must cite an asked outcome or a
    constraint. One that cites none, or only inferred outcomes, becomes a note at the highest severity
    that doesn't block. Returns (findings as they count, what was lowered). An unmarked outcome counts
    as asked; a number the intent doesn't have counts as none. Until 2026-10-01 a finding that cited
    nothing kept blocking, and on tabulate-190 (seeded run 9a3432) Second Eye failed a correct fix on
    an inferred outcome it didn't cite."""
    from dataclasses import replace
    from .lint import outcome_kinds
    kinds = outcome_kinds(intent)
    note = next((s for s in SEVERITIES if s not in blocking), SEVERITIES[-1])
    out, lowered = [], []
    for f in findings:
        cited = [c.split()[-1] for c in f.cites if c.startswith("outcome ")]
        rests = "constraint" in f.cites or any(n in kinds and kinds[n] != "inferred" for n in cited)
        if f.severity in blocking and not rests:
            lowered.append({"text": f.text, "where": f.where, "from": f.severity, "to": note, "cites": list(f.cites),
                            "why": UNASKED})
            f = replace(f, severity=note)
        out.append(f)
    return out, lowered


def section(text: str, name: str) -> str:
    """The body of a '## name' section, stripped."""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def brief(intent: str, review_text: str, tightening: str, diff: str) -> str:
    """Exactly what the checker sees, and nothing else. Each outcome keeps Focus's mark of whose it is:
    asked (the person's words) or inferred (Focus's own addition). Second Eye may fail a change only on
    an asked outcome or a constraint; an inferred one is at most a note (its rules, agents/claude.py).
    Before 2026-09-30 the marks were removed here, and correct fixes failed on inferred outcomes."""
    review_part = review_text.rstrip()
    if tightening.strip():
        review_part += f"\n\n## This task only\n\n{tightening.strip()}"
    from .lint import marked
    return (f"Outcome:\n{marked(section(intent, 'Outcome'))}\n\n"
            f"Constraints:\n{section(intent, 'Constraints')}\n\n"
            f"REVIEW.md:\n{review_part}\n\n"
            f"Diff:\n{diff or '(empty)'}\n")
