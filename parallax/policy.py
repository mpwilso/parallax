"""Policy: the limits, budgets and rules you set for this repo, in parallax.policy.toml.

There are no per-action rulings. Agents do routine work inside their worktree; anything that
crosses the boundary must be in the approved plan; protected paths are never writable; merging
is always yours and isn't a setting. Unknown tables and settings are errors, never ignored.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path

DEFAULT_LIMITS = {"stuck_after": 3, "stale_minutes": 60, "maker_turns": 150}
DEFAULT_BUDGET = {"drafting_usd": 2.0, "small_cap_usd": 5.0, "large_cap_usd": 20.0,  # estimated dollars
                  "small_floor_usd": 2.0, "large_floor_usd": 8.0}  # Maker's spend a cap covers, until the ledger knows (capfloor.py)
DEFAULT_LAUNCH = {"auto_launch_usd": 3.0, "review_paths": [], "review_plans": False}
DEFAULT_DRAFT = {"model": "claude-sonnet-5-5"}  # a fifth of Opus's drafting cost, no more redrafts (docs/plan.md)
DEFAULT_UI_TESTER = {
    "enabled": False, "start": "", "url": "",
    "model": "claude-sonnet-5-5", "max_usd": 0.5,
}
DEFAULT_RETICLE = {"enabled": False, "model": "", "max_usd": 0.5}  # off until the eval says so (docs/evals.md)
DEFAULT_CHECK = {
    "model": "claude-sonnet-5-5",  # a different Claude model from the maker's; the eval measures it
    "diff_cap": 400,               # changed lines a blind review can take reliably
    "rework_cap": 3,               # rework cycles before the task comes to you
    "no_em_dashes": False,         # Parallax's own style rule, on in its own policy only
    "test_command": "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {tests}",
}

DEFAULT_POLICY = """\
# Parallax policy: your rules for this repo. Agents do routine work inside their worktree; anything
# that crosses the boundary must be in the approved plan. Merging is not a setting: it is always yours.
# Costs are estimated US dollars at API list prices, as Claude Code computes them.

[limits]
stuck_after = 3                # the same refused call this many times stops the task
stale_minutes = 60             # a running task silent this long is flagged stuck
maker_turns = 150              # the most turns one maker run may take; hitting it comes to you

[budget]
drafting_usd = 2.00            # the most one drafting call (intent, spec or plan) may spend
small_cap_usd = 5.00           # the highest cap a small task's plan may set
large_cap_usd = 20.00          # the highest cap a large task's plan may set
small_floor_usd = 2.00         # a small task's cap covers at least this much Maker work, until 5 small tasks show better
large_floor_usd = 8.00         # the same for a large task

[launch]
auto_launch_usd = 3.00         # a plan whose cap is at most this launches without asking you, if it crosses no boundary
review_paths = []              # paths or globs: a plan touching any of them waits for your review
review_plans = false           # true: every plan waits for your review

[build]
setup = ""                     # a command, run as you on the base commit, that makes the venv at $PARALLAX_VENV

[draft]
model = "claude-sonnet-5-5"    # the drafters' model: they write the intent, spec and plan

[check]
model = "claude-sonnet-5-5"    # the blind checker's model
diff_cap = 400                 # a bigger diff comes to you to split, or to accept the risk
rework_cap = 3                 # rework cycles before a failing check comes to you
no_em_dashes = false           # true: an em dash added in a changed file is a blocker, found by code
test_command = "python -m pytest -q -p no:cacheprovider -o junit_family=xunit1 --junitxml={junit} {tests}"  # runs the plan's tests

[ui_tester]
enabled = false                # true: a blind agent uses your app in a real browser on user-flow tasks
start = ""                     # starts the app from the built tree's folder, like "npm run dev"
url = ""                       # where the app answers, on this machine only, like "http://127.0.0.1:5173/"
model = "claude-sonnet-5-5"    # the UI tester's model
max_usd = 0.50                 # the most one tester run may spend, and what a plan's cap keeps for it

[reticle]
enabled = false                # true: before the build, an agent writes tests of the intent's outcomes that Maker never sees
model = ""                     # empty: the drafters' model
max_usd = 0.50                 # the most one Reticle run may spend, and what a plan's cap keeps for it
"""


LOCAL_URL = re.compile(r"^http://(127\.0\.0\.1|localhost):\d+(/|$)")


def _ui_tester(cfg: dict) -> dict:
    if set(cfg) - set(DEFAULT_UI_TESTER):
        raise ValueError(f"unknown [ui_tester] settings: {sorted(set(cfg) - set(DEFAULT_UI_TESTER))}")
    out = {**DEFAULT_UI_TESTER, **cfg}
    if not isinstance(out["enabled"], bool):
        raise ValueError("[ui_tester] enabled must be true or false")
    for key in ("start", "url", "model"):
        if not isinstance(out[key], str):
            raise ValueError(f"[ui_tester] {key} must be text")
    if not isinstance(out["max_usd"], (int, float)) or isinstance(out["max_usd"], bool) or out["max_usd"] <= 0:
        raise ValueError("[ui_tester] max_usd must be a dollar amount above 0")
    if out["enabled"]:
        if not out["start"].strip():
            raise ValueError("[ui_tester] needs start: the command that starts your app")
        if not LOCAL_URL.match(out["url"]):
            raise ValueError("[ui_tester] url must be on this machine, like http://127.0.0.1:5173/: the tester's "
                             "network reaches nothing else")
    return out


class Policy:
    def __init__(self, limits: dict[str, int] | None = None, budget: dict[str, float] | None = None,
                 build: dict[str, str] | None = None, check: dict | None = None, launch: dict | None = None,
                 ui_tester: dict | None = None, draft: dict | None = None, reticle: dict | None = None):
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
        if not isinstance(check.get("no_em_dashes", False), bool):
            raise ValueError("[check] no_em_dashes must be true or false")
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
        draft = dict(draft or {})
        if set(draft) - set(DEFAULT_DRAFT):
            raise ValueError(f"unknown [draft] settings: {sorted(set(draft) - set(DEFAULT_DRAFT))}")
        if not isinstance(draft.get("model", "x"), str) or not draft.get("model", "x").strip():
            raise ValueError("[draft] model must be a Claude model name")
        self.draft = {**DEFAULT_DRAFT, **draft}
        reticle = dict(reticle or {})
        if set(reticle) - set(DEFAULT_RETICLE):
            raise ValueError(f"unknown [reticle] settings: {sorted(set(reticle) - set(DEFAULT_RETICLE))}")
        self.reticle = {**DEFAULT_RETICLE, **reticle}
        if not isinstance(self.reticle["enabled"], bool):
            raise ValueError("[reticle] enabled must be true or false")
        if not isinstance(self.reticle["model"], str):
            raise ValueError("[reticle] model must be a Claude model name, or empty for the drafters' model")
        v = self.reticle["max_usd"]
        if not isinstance(v, (int, float)) or isinstance(v, bool) or v <= 0:
            raise ValueError("[reticle] max_usd must be a dollar amount above 0")

    TABLES = ("limits", "budget", "build", "check", "launch", "ui_tester", "draft", "reticle")

    @classmethod
    def from_dict(cls, data: dict) -> "Policy":
        gone = {"actions": "[actions] is gone: routine work in the worktree is allowed, and anything else follows "
                           "the approved plan. remove the table",
                "exact": "[exact] rules are gone with [actions]. remove them",
                "profiles": "profiles are gone. remove [profiles]"}
        for table, why in gone.items():
            if table in data:
                raise ValueError(why)
        unknown = sorted(set(data) - set(cls.TABLES))
        if unknown:
            raise ValueError(f"unknown policy tables: {unknown}")
        return cls(**{t: data.get(t, {}) for t in cls.TABLES})

    @classmethod
    def load(cls, path: Path) -> "Policy":
        with Path(path).open("rb") as f:
            return cls.from_dict(tomllib.load(f))
