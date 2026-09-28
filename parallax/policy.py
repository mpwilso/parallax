"""Policy: what agents may do on their own, what needs a human, what's off limits.

Anything not listed is denied. There is no wildcard allow.
A profile is a complete action table, never an overlay, so nothing is widened implicitly.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ALLOW, ASK, DENY = "allow", "ask", "deny"
RULINGS = {ALLOW, ASK, DENY}
DEFAULT_PROFILE = "default"
READONLY = {"fs.read": ALLOW}  # built in, can't be redefined or widened
DEFAULT_LIMITS = {"max_parallel": 4, "stuck_after": 3, "stale_minutes": 60}

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

# Named profiles are full action tables, picked per task (`task new --profile NAME`).
# "readonly" is built in: fs.read only.
# [profiles.docs.actions]
# "fs.read"  = "allow"
# "fs.write" = "ask"

[limits]
max_parallel  = 4    # tasks running at once
stuck_after   = 3    # the same call refused this many times stops the task
stale_minutes = 60   # a running task silent this long is flagged by pulse
"""


class Policy:
    def __init__(self, actions: dict[str, str], profiles: dict[str, dict[str, str]] | None = None,
                 limits: dict[str, int] | None = None):
        profiles = dict(profiles or {})
        for reserved in ("readonly", DEFAULT_PROFILE):
            if reserved in profiles:
                raise ValueError(f"profile {reserved!r} is built in and can't be redefined")
        self.profiles = {DEFAULT_PROFILE: actions, **profiles, "readonly": dict(READONLY)}
        for name, table in self.profiles.items():
            bad = {a: r for a, r in table.items() if r not in RULINGS}
            if bad:
                raise ValueError(f"unknown rulings in profile {name!r}: {bad}")
            if "git.merge" in table:
                raise ValueError("git.merge can't be set in policy; merging is always a human call")
        limits = dict(limits or {})
        unknown = set(limits) - set(DEFAULT_LIMITS)
        if unknown:
            raise ValueError(f"unknown limits: {sorted(unknown)}")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in limits.values()):
            raise ValueError("limits must be whole numbers of 1 or more")
        self.actions = actions
        self.limits = {**DEFAULT_LIMITS, **limits}

    @classmethod
    def load(cls, path: Path) -> "Policy":
        with Path(path).open("rb") as f:
            data = tomllib.load(f)
        profiles = {name: p.get("actions", {}) for name, p in data.get("profiles", {}).items()}
        return cls(data.get("actions", {}), profiles, data.get("limits", {}))

    def table(self, profile: str = DEFAULT_PROFILE) -> dict[str, str]:
        if profile not in self.profiles:
            raise ValueError(f"unknown profile {profile!r}")
        return self.profiles[profile]

    def ruling(self, action: str, profile: str = DEFAULT_PROFILE) -> str:
        return self.table(profile).get(action, DENY)
