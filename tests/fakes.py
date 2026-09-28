"""Stand-ins for a maker and a checker, so the suite never calls a model."""
from __future__ import annotations

import subprocess
from pathlib import Path

from parallax.agents.base import AgentResult, CheckerError, Verdict


class ScriptedAgent:
    """Plays fixed steps through the permission gate, the way a real maker would.

    Steps: ("write", path, text), ("read", path), ("shell", argv), ("call", fn(cwd)).
    Writes and commands only happen if the gate allows them.
    """

    def __init__(self, steps=(), plan_steps=(), summary="done", plan="1. do it", status="done", cost=None):
        self.steps = {"build": list(steps), "plan": list(plan_steps)}
        self.summary, self.plan, self.status, self.cost = summary, plan, status, cost
        self.goals: list[tuple[str, str]] = []
        self.results: list[tuple[str, str, object]] = []  # (action, detail, Permission)
        self.envs: list[dict] = []

    def run(self, goal, cwd, permission_fn, stage="build", env=None):
        self.goals.append((stage, goal))
        self.envs.append(dict(env or {}))
        cwd = Path(cwd)
        for step in self.steps[stage]:
            kind, p = step[0], None
            if kind == "write":
                p = permission_fn("fs.write", step[1], [step[1]])
                if p.allowed:
                    target = Path(step[1]) if Path(step[1]).is_absolute() else cwd / step[1]
                    target.write_text(step[2])
                self.results.append(("fs.write", step[1], p))
            elif kind == "read":
                p = permission_fn("fs.read", step[1], [])
                self.results.append(("fs.read", step[1], p))
            elif kind == "shell":
                p = permission_fn("shell.run", " ".join(step[1]), [])
                if p.allowed:
                    subprocess.run(step[1], cwd=cwd, capture_output=True)  # a failed command is output, not a crash
                self.results.append(("shell.run", " ".join(step[1]), p))
            elif kind == "call":
                step[1](cwd)
            if p is not None and p.stop:  # like the SDK ending the agent from the hook
                return AgentResult("error", p.message)
        return AgentResult(self.status, self.plan if stage == "plan" else self.summary, self.cost)


class FakeChecker:
    def __init__(self, verdict="pass", findings=(), plan_verdict="pass", error=False):
        self.verdict, self.plan_verdict = verdict, plan_verdict
        self.findings, self.error = list(findings), error
        self.calls: list[tuple[str, str, str]] = []  # (goal, material, kind)

    def review(self, goal, material, kind):
        self.calls.append((goal, material, kind))
        if self.error:
            raise CheckerError("garbled reply")
        return Verdict(self.plan_verdict if kind == "plan" else self.verdict, self.findings)
