"""Policy: what agents may do on their own, what needs a human, what's off limits.

Anything not listed is denied. There is no wildcard allow.
Exact rules match one exact detail (a command, a path) and win over the action-level ruling.
The eval policy uses them to allow only the test command.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

ALLOW, ASK, DENY = "allow", "ask", "deny"
RULINGS = {ALLOW, ASK, DENY}
# promote_after, evidence_days and law_after are kept for earned autonomy (M14); nothing reads them yet
DEFAULT_LIMITS = {
    "max_parallel": 4, "stuck_after": 3, "stale_minutes": 60,
    "promote_after": 10, "evidence_days": 30, "law_after": 3,
}
DEFAULT_BUDGET = {"drafting_usd": 2.0}  # estimated dollars
WORKTREE_TOKEN = "<worktree>"

DEFAULT_POLICY = """\
# Parallax policy. Anything not listed here is denied.
# allow = agent may do it, logged
# ask   = becomes a pending decision in your inbox
# deny  = refused, logged
#
# Merging is not a policy setting. It is always a human decision.

[actions]
"fs.read"   = "allow"
"fs.write"  = "ask"
"shell.run" = "ask"
"git.commit" = "ask"
"net.fetch" = "deny"
"git.push"  = "deny"

# Exact rules match one exact command or path and win over [actions].
# [exact."shell.run"]
# "pytest -q" = "allow"

[limits]
max_parallel  = 4    # tasks running at once
stuck_after   = 3    # the same call refused this many times stops the task
stale_minutes = 60   # a running task silent this long is flagged stuck

[budget]
# estimated US dollars at API list prices, as Claude Code computes them. not a charge.
drafting_usd = 2.00  # the most one drafting call (intent, spec or plan) may use
"""


def normalize_detail(action: str, detail: str, worktree: str | Path | None = None) -> str:
    """The same request from different tasks gets the same key, so exact rules line up."""
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
    def __init__(self, actions: dict[str, str], limits: dict[str, int] | None = None,
                 exact: dict[str, dict[str, str]] | None = None, budget: dict[str, float] | None = None):
        _check_table("[actions]", actions)
        self.exact = {action: dict(table) for action, table in (exact or {}).items() if table}
        if "git.merge" in self.exact:
            raise ValueError("git.merge can't be set in policy; merging is always a human call")
        for action, table in self.exact.items():
            _check_table(f"exact rules for {action!r}", table)

        limits = dict(limits or {})
        unknown = set(limits) - set(DEFAULT_LIMITS)
        if unknown:
            raise ValueError(f"unknown limits: {sorted(unknown)}")
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in limits.values()):
            raise ValueError("limits must be whole numbers of 1 or more")
        budget = dict(budget or {})
        unknown = set(budget) - set(DEFAULT_BUDGET)
        if unknown:
            raise ValueError(f"unknown budget settings: {sorted(unknown)}")
        if any(not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0 for v in budget.values()):
            raise ValueError("budget settings must be dollar amounts above 0")
        self.actions = actions
        self.limits = {**DEFAULT_LIMITS, **limits}
        self.budget = {**DEFAULT_BUDGET, **budget}

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        if "profiles" in data:
            raise ValueError("profiles are gone. every task uses [actions]; remove [profiles]")
        return cls(data.get("actions", {}), data.get("limits", {}), data.get("exact", {}), data.get("budget", {}))

    @classmethod
    def load(cls, path: Path) -> "Policy":
        with Path(path).open("rb") as f:
            return cls.from_dict(tomllib.load(f))

    def exact_ruling(self, action: str, key: str) -> str | None:
        return self.exact.get(action, {}).get(key)

    def ruling(self, action: str, key: str | None = None) -> str:
        if key is not None:
            exact = self.exact_ruling(action, key)
            if exact:
                return exact
        return self.actions.get(action, DENY)

    def listed(self, action: str, key: str | None = None) -> bool:
        return action in self.actions or (key is not None and self.exact_ruling(action, key) is not None)
