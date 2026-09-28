"""Policy: what agents may do on their own, what needs a human, what's off limits.

Anything not listed is denied. There is no wildcard allow.
A profile is a complete action table, never an overlay, so nothing is widened implicitly.
Exact rules match one exact detail (a command, a path) and win over the action-level ruling.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

ALLOW, ASK, DENY = "allow", "ask", "deny"
RULINGS = {ALLOW, ASK, DENY}
STRICTNESS = {ALLOW: 0, ASK: 1, DENY: 2}
DEFAULT_PROFILE = "default"
READONLY = {"fs.read": ALLOW}  # built in, can't be redefined or widened
DEFAULT_LIMITS = {
    "max_parallel": 4, "stuck_after": 3, "stale_minutes": 60,
    "promote_after": 10, "evidence_days": 30, "law_after": 3,
}
WORKTREE_TOKEN = "<worktree>"

DEFAULT_POLICY = """\
# Parallax policy. Anything not listed here is denied.
# allow = agent may do it, logged
# ask   = becomes a pending decision in your inbox
# deny  = refused, logged
#
# Promotions (ask -> allow) are proposed from evidence and approved by a human.
# Merging is not a policy setting. It is always a human decision.

[actions]
"fs.read"   = "allow"
"fs.write"  = "ask"
"shell.run" = "ask"
"git.commit" = "ask"
"net.fetch" = "deny"
"git.push"  = "deny"

# Exact rules match one exact command or path and win over [actions].
# Approved promotions and laws are written here by parallax.
# [exact."shell.run"]
# "pytest -q" = "allow"

# Named profiles are full action tables, picked per task (`task new --profile NAME`).
# "readonly" is built in: fs.read only.
# [profiles.docs.actions]
# "fs.read"  = "allow"
# "fs.write" = "ask"

[limits]
max_parallel  = 4    # tasks running at once
stuck_after   = 3    # the same call refused this many times stops the task
stale_minutes = 60   # a running task silent this long is flagged by pulse
promote_after = 10   # approvals, with no rejections, before a promotion is proposed
evidence_days = 30   # how far back evidence counts
law_after     = 3    # rejections sharing a cause before a law is proposed
"""


def normalize_detail(action: str, detail: str, worktree: str | Path | None = None) -> str:
    """The same request from different tasks gets the same key, so evidence and exact rules line up."""
    detail = detail.strip()
    if not worktree:
        return detail
    wt = os.path.normpath(str(worktree))
    if action.startswith("fs.") and os.path.isabs(detail):
        full = os.path.normpath(detail)
        if os.path.normcase(full).startswith(os.path.normcase(wt) + os.sep):
            return os.path.relpath(full, wt).replace(os.sep, "/")
        return detail
    for form in {wt, Path(wt).as_posix()}:
        detail = re.sub(re.escape(form), WORKTREE_TOKEN, detail, flags=re.IGNORECASE)
    return detail


def _check_table(where: str, table: dict[str, str]) -> None:
    bad = {a: r for a, r in table.items() if not isinstance(r, str) or r not in RULINGS}
    if bad:
        raise ValueError(f"unknown rulings in {where}: {bad}")
    if "git.merge" in table:
        raise ValueError("git.merge can't be set in policy; merging is always a human call")


class Policy:
    def __init__(self, actions: dict[str, str], profiles: dict[str, dict[str, str]] | None = None,
                 limits: dict[str, int] | None = None, exact: dict[str, dict[str, dict[str, str]]] | None = None):
        profiles = dict(profiles or {})
        for reserved in ("readonly", DEFAULT_PROFILE):
            if reserved in profiles:
                raise ValueError(f"profile {reserved!r} is built in and can't be redefined")
        self.profiles = {DEFAULT_PROFILE: actions, **profiles, "readonly": dict(READONLY)}
        for name, table in self.profiles.items():
            _check_table(f"profile {name!r}", table)

        self.exact = {name: dict(tables) for name, tables in (exact or {}).items() if tables}
        for name, tables in self.exact.items():
            if name == "readonly":
                raise ValueError("the readonly profile can't take exact rules")
            if name not in self.profiles:
                raise ValueError(f"exact rules for unknown profile {name!r}")
            if "git.merge" in tables:
                raise ValueError("git.merge can't be set in policy; merging is always a human call")
            for action, table in tables.items():
                _check_table(f"exact rules for {action!r}", table)

        limits = dict(limits or {})
        unknown = set(limits) - set(DEFAULT_LIMITS)
        if unknown:
            raise ValueError(f"unknown limits: {sorted(unknown)}")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in limits.values()):
            raise ValueError("limits must be whole numbers of 1 or more")
        self.actions = actions
        self.limits = {**DEFAULT_LIMITS, **limits}

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        profiles = data.get("profiles", {})
        exact = {DEFAULT_PROFILE: data.get("exact", {})}
        exact.update({name: p.get("exact", {}) for name, p in profiles.items()})
        return cls(data.get("actions", {}), {name: p.get("actions", {}) for name, p in profiles.items()},
                   data.get("limits", {}), exact)

    @classmethod
    def load(cls, path: Path) -> "Policy":
        with Path(path).open("rb") as f:
            return cls.from_dict(tomllib.load(f))

    def table(self, profile: str = DEFAULT_PROFILE) -> dict[str, str]:
        if profile not in self.profiles:
            raise ValueError(f"unknown profile {profile!r}")
        return self.profiles[profile]

    def exact_ruling(self, action: str, key: str, profile: str = DEFAULT_PROFILE) -> str | None:
        return self.exact.get(profile, {}).get(action, {}).get(key)

    def ruling(self, action: str, profile: str = DEFAULT_PROFILE, key: str | None = None) -> str:
        table = self.table(profile)
        if key is not None:
            exact = self.exact_ruling(action, key, profile)
            if exact:
                return exact
        return table.get(action, DENY)

    def listed(self, action: str, profile: str = DEFAULT_PROFILE, key: str | None = None) -> bool:
        return action in self.table(profile) or (key is not None and self.exact_ruling(action, key, profile) is not None)
