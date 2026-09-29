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
DEFAULT_BUDGET = {"drafting_usd": 2.0, "small_cap_usd": 5.0, "large_cap_usd": 20.0}  # estimated dollars
DEFAULT_LAUNCH = {"auto_launch_usd": 3.0, "review_paths": [], "review_plans": False}
DEFAULT_UI_TESTER = {
    "enabled": False, "start": "", "url": "", "paths": [], "tests": "tests/ui_flows",
    "model": "claude-sonnet-5-5", "max_usd": 1.5,
}
DEFAULT_CHECK = {
    "model": "claude-sonnet-5-5",  # a different Claude model from the maker's; the eval measures it
    "diff_cap": 400,               # changed lines a blind review can take reliably
    "rework_cap": 3,               # rework cycles before the task comes to you
    "test_command": "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {tests}",
}
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
drafting_usd = 2.00   # the most one drafting call (intent, spec or plan) may use
small_cap_usd = 5.00  # the most a small task's plan may set as its cap
large_cap_usd = 20.00 # the same, for a large task

[launch]
# when code may approve a plan and start the build without you. everything else waits for you.
auto_launch_usd = 3.00  # a plan whose budget cap is at most this launches on its own
review_paths = []       # paths or globs: a plan touching any of them waits for you to review it
review_plans = false    # true: every plan waits for you

[build]
# a shell command that makes the task's Python environment before the build. it runs as you,
# outside the sandbox, in the task's worktree, with $PARALLAX_VENV set to where the venv goes.
# the maker gets the venv on its PATH and can read it, nothing more. empty: no venv.
setup = ""

[check]
model = "claude-sonnet-5-5"  # the blind checker, a different Claude model from the maker
diff_cap = 400               # a bigger diff comes to you to split, or to accept the risk
rework_cap = 3               # rework cycles before a failing check comes to you
# test_command = "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {tests}"

[ui_tester]
# a blind agent that uses your app in a real browser after the build, on tasks whose plan changes
# a UI, and leaves Playwright tests that every later check reruns with no model. off until you
# turn it on here. it runs in the sandbox, with network only to the app's local address.
enabled = false
start = ""                 # starts the app, from the built tree's folder, e.g. "npm run dev"
url = ""                   # where the app answers, on this machine only, e.g. "http://127.0.0.1:5173/"
paths = []                 # a plan changing any of these paths or globs changes the UI
# tests = "tests/ui_flows" # where its tests go
# max_usd = 1.50           # the most one run of the tester may use, inside the task's cap
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


LOCAL_URL = re.compile(r"^http://(127\.0\.0\.1|localhost):\d+(/|$)")


def _ui_tester(cfg: dict) -> dict:
    if set(cfg) - set(DEFAULT_UI_TESTER):
        raise ValueError(f"unknown [ui_tester] settings: {sorted(set(cfg) - set(DEFAULT_UI_TESTER))}")
    out = {**DEFAULT_UI_TESTER, **cfg}
    if not isinstance(out["enabled"], bool):
        raise ValueError("[ui_tester] enabled must be true or false")
    for key in ("start", "url", "tests", "model"):
        if not isinstance(out[key], str):
            raise ValueError(f"[ui_tester] {key} must be text")
    if not isinstance(out["paths"], list) or not all(isinstance(x, str) and x.strip() for x in out["paths"]):
        raise ValueError("[ui_tester] paths must be a list of paths or globs")
    if not isinstance(out["max_usd"], (int, float)) or isinstance(out["max_usd"], bool) or out["max_usd"] <= 0:
        raise ValueError("[ui_tester] max_usd must be a dollar amount above 0")
    tests = out["tests"].strip("/")
    if not tests or tests.startswith((".", "/")) or ".." in tests.split("/") or tests.split("/")[0] == "docs":
        raise ValueError("[ui_tester] tests must be a folder inside the repo, like tests/ui_flows")
    out["tests"] = tests
    if out["enabled"]:
        if not out["start"].strip():
            raise ValueError("[ui_tester] needs start: the command that starts your app")
        if not LOCAL_URL.match(out["url"]):
            raise ValueError("[ui_tester] url must be on this machine, like http://127.0.0.1:5173/: the tester's "
                             "network reaches nothing else")
        if not out["paths"]:
            raise ValueError("[ui_tester] needs paths: which files make up your UI, so Parallax knows a plan changes it")
    return out


def _check_table(where: str, table: dict[str, str]) -> None:
    bad = {a: r for a, r in table.items() if not isinstance(r, str) or r not in RULINGS}
    if bad:
        raise ValueError(f"unknown rulings in {where}: {bad}")
    if "git.merge" in table:
        raise ValueError("git.merge can't be set in policy; merging is always a human call")


class Policy:
    def __init__(self, actions: dict[str, str], limits: dict[str, int] | None = None,
                 exact: dict[str, dict[str, str]] | None = None, budget: dict[str, float] | None = None,
                 build: dict[str, str] | None = None, check: dict | None = None, launch: dict | None = None,
                 ui_tester: dict | None = None):
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
        build = dict(build or {})
        if set(build) - {"setup"} or not isinstance(build.get("setup", ""), str):
            raise ValueError("[build] takes one setting: setup, a shell command")
        self.build = {"setup": build.get("setup", "")}
        check = dict(check or {})
        if set(check) - set(DEFAULT_CHECK):
            raise ValueError(f"unknown [check] settings: {sorted(set(check) - set(DEFAULT_CHECK))}")
        for key in ("diff_cap", "rework_cap"):
            v = check.get(key, DEFAULT_CHECK[key])
            if not isinstance(v, int) or isinstance(v, bool) or v < 1:
                raise ValueError(f"[check] {key} must be a whole number of 1 or more")
        for key in ("model", "test_command"):
            if not isinstance(check.get(key, ""), str):
                raise ValueError(f"[check] {key} must be text")
        self.check = {**DEFAULT_CHECK, **check}
        launch = dict(launch or {})
        if set(launch) - set(DEFAULT_LAUNCH):
            raise ValueError(f"unknown [launch] settings: {sorted(set(launch) - set(DEFAULT_LAUNCH))}")
        v = launch.get("auto_launch_usd", DEFAULT_LAUNCH["auto_launch_usd"])
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v < 0:
            raise ValueError("[launch] auto_launch_usd must be a dollar amount of 0 or more (0: always ask)")
        paths = launch.get("review_paths", [])
        if not isinstance(paths, list) or not all(isinstance(x, str) and x.strip() for x in paths):
            raise ValueError("[launch] review_paths must be a list of paths or globs")
        if not isinstance(launch.get("review_plans", False), bool):
            raise ValueError("[launch] review_plans must be true or false")
        self.launch = {**DEFAULT_LAUNCH, **launch}
        self.ui_tester = _ui_tester(dict(ui_tester or {}))

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        if "profiles" in data:
            raise ValueError("profiles are gone. every task uses [actions]; remove [profiles]")
        return cls(data.get("actions", {}), data.get("limits", {}), data.get("exact", {}), data.get("budget", {}),
                   data.get("build", {}), data.get("check", {}),
                   data.get("launch", {}), data.get("ui_tester", {}))

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
