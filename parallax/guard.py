"""Invariant 9: agents never write the rules that govern them.

These checks run before policy is consulted, so no policy setting can switch them off.
Shell commands can't be parsed reliably, so the string check is best effort and the
fingerprint of the real files is the backstop.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePath

from .core import POLICY_FILE, STATE_DIR

MISSION_FILE = "mission.md"  # the mission feature is gone; the name stays protected
PROTECTED_NAMES = {POLICY_FILE, MISSION_FILE}


def _protected_parts(parts: tuple[str, ...]) -> bool:
    parts = tuple(p.casefold() for p in parts)
    return bool(parts) and (STATE_DIR in parts or parts[-1] in PROTECTED_NAMES)


def check_write(path: str, worktree: Path) -> str | None:
    """Return why a write is refused, or None if the guard has no objection."""
    target = Path(path)
    if not target.is_absolute():
        target = Path(worktree) / target
    full = os.path.normcase(str(target.resolve()))
    root = os.path.normcase(str(Path(worktree).resolve()))
    if full != root and not full.startswith(root + os.sep):
        return "outside the task worktree"
    if _protected_parts(PurePath(full[len(root):].lstrip(os.sep)).parts):
        return "protected file (policy, mission, or .parallax/)"
    return None


def check_shell(command: str, worktree: Path) -> str | None:
    """Refuse commands that mention a protected file. False positives are fine."""
    text = command.casefold()
    if any(name in text for name in (*PROTECTED_NAMES, STATE_DIR)):
        return "command mentions a protected file (policy, mission, or .parallax/)"
    return None


def protected_in(paths: list[str]) -> list[str]:
    """Which of these repo-relative paths (e.g. from `git diff --name-only`) are protected."""
    return [p for p in paths if _protected_parts(PurePath(p).parts)]


def fingerprint(root: Path) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in sorted(PROTECTED_NAMES):
        f = Path(root) / name
        out[name] = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    return out
