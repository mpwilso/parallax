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


def section(text: str, name: str) -> str:
    """The body of a '## name' section, stripped."""
    m = re.search(rf"^##\s+{re.escape(name)}\s*$\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def brief(intent: str, review_text: str, tightening: str, diff: str) -> str:
    """Exactly what the checker sees, and nothing else."""
    review_part = review_text.rstrip()
    if tightening.strip():
        review_part += f"\n\n## This task only\n\n{tightening.strip()}"
    return (f"Outcome:\n{section(intent, 'Outcome')}\n\n"
            f"Constraints:\n{section(intent, 'Constraints')}\n\n"
            f"REVIEW.md:\n{review_part}\n\n"
            f"Diff:\n{diff or '(empty)'}\n")
