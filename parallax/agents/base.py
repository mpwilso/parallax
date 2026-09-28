"""What the core expects from a maker and a checker. No vendor imports here.

A maker works a task inside its worktree and asks permission_fn before every action.
A checker sees only the task goal and the material under review (a plan or a diff).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol

VERDICTS = {"pass", "fail", "no_finding"}
AGREE = {"pass", "no_finding"}  # no_finding is a real answer, not a failure


class AgentUnavailable(Exception):
    """The adapter can't run here (for example, its SDK isn't installed)."""


class CheckerError(Exception):
    """The checker gave no usable verdict."""


@dataclass
class Permission:
    allowed: bool
    message: str = ""
    stop: bool = False  # the task is stuck or tripped a guard: end the agent now


# (action, detail, paths) -> Permission
PermissionFn = Callable[[str, str, list[str]], Permission]


@dataclass
class AgentResult:
    status: str  # "done" | "gave_up" | "error"
    summary: str
    cost_usd: float | None = None


@dataclass
class Verdict:
    verdict: str  # one of VERDICTS
    findings: list[str] = field(default_factory=list)
    cost_usd: float | None = None


class Agent(Protocol):
    def run(self, goal: str, cwd: Path, permission_fn: PermissionFn, stage: str = "build",
            env: dict[str, str] | None = None) -> AgentResult: ...


class Checker(Protocol):
    def review(self, goal: str, material: str, kind: str) -> Verdict: ...
