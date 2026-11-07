"""The split, checked by code. Focus proposes how one request divides into parts; code decides
whether the split holds, and in what order the parts run. No model decides either.

A split holds when:
- every part has a goal in plain words and a scope that isn't empty;
- no scope names a protected path, or a path inside one: no part may ever write them;
- it has at most [conductor] max_parts parts.
Scopes may overlap, because code orders those parts: parts whose scopes overlap, directly or through
another part, run one after another in the split's order, each made on the one before it's accepted
commit (order). Parts that don't overlap run side by side. One part is always allowed.
"""
from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath

from .guard import is_protected

WILD = "*?["


def _norm(pattern: str) -> str:
    p = pattern.strip().strip("`").replace("\\", "/").removeprefix("./").lstrip("/")
    return p + "*" if p.endswith("/") else p  # a folder: everything under it


def _literal(pattern: str) -> str:
    """The part of a pattern before its first wildcard: every path it matches starts with this."""
    cut = min((pattern.index(c) for c in WILD if c in pattern), default=len(pattern))
    return pattern[:cut]


def _touch(p: str, q: str) -> bool:
    """Could one path match both patterns? Exact for a plain path; for two globs, true whenever their
    plain beginnings could meet. It errs toward overlap: a false overlap only runs parts in turn."""
    p, q = _norm(p), _norm(q)
    if p == q or fnmatch(p, q) or fnmatch(q, p):
        return True
    lp, lq = _literal(p), _literal(q)
    if lp != p and lq != q:
        return lp.startswith(lq) or lq.startswith(lp)
    return False


def overlaps(a: list[str], b: list[str]) -> bool:
    return any(_touch(p, q) for p in a for q in b)


def protected(pattern: str) -> bool:
    """A scope entry that is a protected path, or lies inside one (docs/tasks/**, .claude/*)."""
    p = _norm(pattern)
    head = _literal(p).rstrip("/")
    return is_protected(p) or (head != p and bool(head) and is_protected(head))


def problems(parts: list[dict], max_parts: int) -> list[str]:
    """What's wrong with the split, each in words Focus can act on. Empty: it holds."""
    out = []
    if len(parts) > max_parts:
        out.append(f"the split has {len(parts)} parts, and the policy allows at most {max_parts} "
                   "([conductor] max_parts): join parts, or make it one part")
    for part in parts:
        n = part["n"]
        if not (part.get("goal") or "").strip():
            out.append(f"part {n} has no goal: say what it does, in plain words")
        if not part.get("scope"):
            out.append(f"part {n} has no scope: list the paths or globs it may touch, tests included")
        for s in part.get("scope") or []:
            pp = PurePosixPath(s.replace("\\", "/"))
            if pp.is_absolute() or ".." in pp.parts:
                out.append(f"part {n}'s scope names {s}, which isn't a path inside the repo")
            elif protected(s):
                out.append(f"part {n}'s scope names {s}, which is protected: no part may ever write it")
    return out


def order(parts: list[dict]) -> dict[int, int | None]:
    """part number -> the part it runs after, or None to start at once. Parts joined by overlapping
    scopes, directly or through another part, form one line in the split's order."""
    group = {p["n"]: p["n"] for p in parts}

    def root(n: int) -> int:
        while group[n] != n:
            n = group[n]
        return n
    for i, a in enumerate(parts):
        for b in parts[i + 1:]:
            if overlaps(a.get("scope") or [], b.get("scope") or []):
                group[root(b["n"])] = root(a["n"])
    last: dict[int, int] = {}
    out: dict[int, int | None] = {}
    for p in parts:
        r = root(p["n"])
        out[p["n"]] = last.get(r)
        last[r] = p["n"]
    return out
