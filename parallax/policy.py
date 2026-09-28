"""Policy: what agents may do on their own, what needs a human, what's off limits.

Anything not listed is denied. There is no wildcard allow.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

ALLOW, ASK, DENY = "allow", "ask", "deny"
RULINGS = {ALLOW, ASK, DENY}

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
"""


class Policy:
    def __init__(self, actions: dict[str, str]):
        bad = {a: r for a, r in actions.items() if r not in RULINGS}
        if bad:
            raise ValueError(f"unknown rulings: {bad}")
        if "git.merge" in actions:
            raise ValueError("git.merge can't be set in policy; merging is always a human call")
        self.actions = actions

    @classmethod
    def load(cls, path: Path) -> "Policy":
        with Path(path).open("rb") as f:
            return cls(tomllib.load(f).get("actions", {}))

    def ruling(self, action: str) -> str:
        return self.actions.get(action, DENY)
