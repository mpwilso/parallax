"""Invariant 9: agents never write the rules that govern them, or the protected paths.

These checks are the tool layer. They run before policy is consulted, so no policy setting can
switch them off. From M9 the OS sandbox is the second layer for Bash (see sandbox.py). Shell
commands can't be parsed reliably, so the string check is best effort; the sandbox and the check
of the final diff are what hold.
"""
from __future__ import annotations

import hashlib
import os
import re
import shlex
from pathlib import Path, PurePath

from .core import POLICY_FILE, STATE_DIR

MISSION_FILE = "mission.md"  # the mission feature is gone; the name stays protected
PROTECTED_NAMES = {POLICY_FILE, MISSION_FILE}  # the rules files Parallax reads, fingerprinted per run

# The brief's full list, plus the worktree's own .git pointer file (".git" at any depth).
PROTECTED_ANY_DEPTH = {STATE_DIR, ".git", ".claude", POLICY_FILE, MISSION_FILE, "claude.md", ".mcp.json", "review.md"}
PROTECTED_FROM_ROOT = (("docs", "parallax.md"), ("docs", "tasks"))
_ANY_DEPTH = {n.casefold() for n in PROTECTED_ANY_DEPTH}


def is_protected(rel: str) -> bool:
    """Is this repo-relative path one the maker may never write? Case-insensitive, fails safe."""
    parts = tuple(p.casefold() for p in PurePath(rel.replace("\\", "/")).parts if p not in ("", "."))
    if not parts:
        return False
    if any(p in _ANY_DEPTH for p in parts):
        return True
    return any(parts[:len(prefix)] == prefix for prefix in PROTECTED_FROM_ROOT)


def _inside(target: Path, root: Path) -> str | None:
    """target's path relative to root, or None if it isn't inside root."""
    full = os.path.normcase(str(target.resolve()))
    base = os.path.normcase(str(Path(root).resolve()))
    if full == base:
        return ""
    if full.startswith(base + os.sep):
        return full[len(base) + 1:]
    return None


def check_write(path: str, worktree: Path) -> str | None:
    """Return why a write is refused, or None if the guard has no objection."""
    target = Path(path)
    if not target.is_absolute():
        target = Path(worktree) / target
    rel = _inside(target, worktree)
    if rel is None:
        return "outside the task worktree"
    if is_protected(rel):
        return "protected file (CLAUDE.md, .claude/, .git, docs/tasks/, the policy, and the like)"
    return None


def check_read(target: str, worktree: Path, also: tuple[Path, ...] = ()) -> str | None:
    """Reads stay inside the worktree, plus any roots in also (the task's venv, the plan's reads).

    A glob is judged by the part before its first wildcard. Empty means the worktree itself.
    """
    base = re.split(r"[*?\[{]", target, maxsplit=1)[0] if target else ""
    t = Path(base) if base else Path(".")
    if not t.is_absolute():
        t = Path(worktree) / t
    if any(_inside(t, root) is not None for root in (Path(worktree), *also)):
        return None
    return "read outside the task worktree"


def _tokens(command: str) -> list[str]:
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    out = []
    for w in words:
        out += [t for t in re.split(r"[<>|;&=()`]+", w) if t]
    return out


def check_shell(command: str, worktree: Path) -> str | None:
    """Refuse commands that name a protected path. False positives are fine."""
    for token in _tokens(command):
        t = Path(token)
        rel = _inside(t if t.is_absolute() else Path(worktree) / t, worktree)
        if is_protected(rel if rel is not None else token):
            return f"command names a protected path: {token}"
    return None


def protected_in(paths: list[str]) -> list[str]:
    """Which of these repo-relative paths (e.g. from `git diff --name-only`) are protected."""
    return [p for p in paths if is_protected(p)]


def fingerprint(root: Path) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in sorted(PROTECTED_NAMES):
        f = Path(root) / name
        out[name] = hashlib.sha256(f.read_bytes()).hexdigest() if f.exists() else None
    return out
