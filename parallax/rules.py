"""The only code that writes parallax.policy.toml and mission.md, and only on a human's approval.

Every edit is verified by parsing before it's written: the one intended change and nothing else.
Every write is recorded as rules.changed (hash before, hash after) before the file changes, so a
running gate can tell a recorded change from tampering (see trail_ok).
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

from . import mission as missions
from .core import POLICY_FILE, ParallaxError, Project
from .policy import DEFAULT_PROFILE, Policy

HEADER = re.compile(r"^\s*\[(?!\[).*\]\s*(#.*)?$")


class RulesEditError(ParallaxError):
    def __init__(self, what: str, snippet: str):
        super().__init__(f"couldn't safely edit {what}. nothing was changed or resolved. add this by hand:\n{snippet}")
        self.snippet = snippet


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _q(part: str) -> str:
    return part if re.fullmatch(r"[A-Za-z0-9_-]+", part) else json.dumps(part)


def _header_path(line: str) -> tuple[str, ...] | None:
    try:
        node = tomllib.loads(line.strip())
    except tomllib.TOMLDecodeError:
        return None
    path = []
    while isinstance(node, dict) and len(node) == 1:
        (k, node), = node.items()
        path.append(k)
    return tuple(path)


def _line_key(line: str) -> str | None:
    try:
        parsed = tomllib.loads(line)
    except tomllib.TOMLDecodeError:
        return None
    if len(parsed) == 1:
        (k, v), = parsed.items()
        return None if isinstance(v, dict) else k
    return None


def _set_in(text: str, table: tuple[str, ...], name: str, line: str) -> str:
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines()
    headers = [(i, _header_path(l)) for i, l in enumerate(lines) if HEADER.match(l)]
    for n, (i, path) in enumerate(headers):
        if path != table:
            continue
        end = headers[n + 1][0] if n + 1 < len(headers) else len(lines)
        insert_at = i + 1
        for j in range(i + 1, end):
            if _line_key(lines[j]) == name:
                lines[j] = line
                return nl.join(lines) + nl
            if lines[j].strip() and not lines[j].strip().startswith("#"):
                insert_at = j + 1
        lines.insert(insert_at, line)
        return nl.join(lines) + nl
    lines += ["", f"[{'.'.join(_q(p) for p in table)}]", line]
    return nl.join(lines) + nl


def write(project: Project, name: str, text: str, item: str) -> None:
    """Record the change, then make it. Atomic replace, so readers never see half a file."""
    path = project.root / name
    data = text.encode("utf-8")
    before = _sha(path.read_bytes()) if path.exists() else None
    project.ledger.append("rules.changed", "human", f"{name} updated", file=name,
                          before=before, after=_sha(data), item=item)
    tmp = path.with_name(path.name + ".parallax-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def trail_ok(project: Project, old: dict[str, str | None], new: dict[str, str | None]) -> bool:
    """True if every protected file that changed got there through recorded rules.changed steps."""
    changes = [e["data"] for e in project.ledger.entries() if e["kind"] == "rules.changed"]
    for name in new:
        if old.get(name) == new[name]:
            continue
        h = old.get(name)
        for d in changes:
            if d["file"] == name and d["before"] == h:
                h = d["after"]
        if h != new[name]:
            return False
    return True


def set_policy_rule(project: Project, profile: str, action: str, key: str | None, ruling: str,
                    item: str) -> str:
    """Set one rule, action-level (key None) or exact. Returns a short description of the change."""
    table = ("exact", action) if key is not None else ("actions",)
    if profile != DEFAULT_PROFILE:
        table = ("profiles", profile) + table
    name = key if key is not None else action
    line = f"{json.dumps(name)} = {json.dumps(ruling)}  # ledger {item}"
    header = f"[{'.'.join(_q(p) for p in table)}]"
    what = f"{'exact ' if key is not None else ''}{action}{' ' + json.dumps(key) if key is not None else ''} = {ruling}"

    path = project.root / POLICY_FILE
    old = path.read_bytes().decode("utf-8")
    new = _set_in(old, table, name, line)
    try:
        before, after = tomllib.loads(old), tomllib.loads(new)
        expected = copy.deepcopy(before)
        node = expected
        for part in table:
            node = node.setdefault(part, {})
        node[name] = ruling
        ok = after == expected
        Policy.from_dict(after)
    except (tomllib.TOMLDecodeError, ValueError, AttributeError, TypeError):
        ok = False
    if not ok:
        raise RulesEditError(POLICY_FILE, f"{header}\n{line}")
    write(project, POLICY_FILE, new, item)
    project.reload_policy()
    return f"policy updated: {what}"


def add_law(project: Project, text: str, item: str) -> str:
    """Append a prose law to mission.md's how section."""
    text = " ".join(text.split())
    line = f"- {text} <!-- ledger {item} -->"
    path = project.root / missions.MISSION_FILE
    old = path.read_bytes().decode("utf-8") if path.exists() else ""
    nl = "\r\n" if "\r\n" in old else "\n"
    lines = old.splitlines()
    start = next((i for i, l in enumerate(lines) if re.match(r"^##\s+how\s*$", l.strip(), re.I)), None)
    if start is None:
        lines += ["", "## how", line]
    else:
        end = next((j for j in range(start + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
        while end > start + 1 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, line)
    new = nl.join(lines) + nl

    before, after = missions.parse(old), missions.parse(new)
    ok = after is not None and after.how.endswith(f"- {text}") and (
        before is None or (before.who, before.what) == (after.who, after.what))
    if not ok:
        raise RulesEditError(missions.MISSION_FILE, f"## how\n{line}")
    write(project, missions.MISSION_FILE, new, item)
    return f"mission.md updated: law added to how"
