"""Stand-ins for a maker and a checker, so the suite never calls a model."""
from __future__ import annotations

import subprocess
from pathlib import Path

from parallax.agents.base import AgentResult, CheckerError, Finding, Review, Verdict


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
    """reviews: what check() answers, in order; the last one repeats."""

    def __init__(self, verdict="pass", findings=(), plan_verdict="pass", error=False, reviews=None):
        self.verdict, self.plan_verdict = verdict, plan_verdict
        self.findings, self.error = list(findings), error
        self.calls: list[tuple[str, str, str]] = []  # (goal, material, kind)
        self.reviews = list(reviews or [Review("pass")])
        self.briefs: list[str] = []
        self.models: list[str] = []

    def __call__(self, left, model):  # the checker factory: budget left and the policy's model
        self.models.append(model)
        return self

    def check(self, brief):
        self.briefs.append(brief)
        if self.error:
            raise CheckerError("garbled reply")
        return self.reviews[min(len(self.briefs), len(self.reviews)) - 1]

    def review(self, goal, material, kind):
        self.calls.append((goal, material, kind))
        if self.error:
            raise CheckerError("garbled reply")
        return Verdict(self.plan_verdict if kind == "plan" else self.verdict, self.findings)


class FakeDrafter:
    """Returns canned file text per doc ("intent", "spec", "plan"), read from the request's first line.

    reads: paths it tries to read through the gate first. fail: docs it fails on.
    """

    def __init__(self, docs, reads=(), fail=(), cost=0.1):
        self.docs, self.reads, self.fail, self.cost = dict(docs), list(reads), set(fail), cost
        self.requests: list[str] = []
        self.results: list[tuple[str, object]] = []  # (path, Permission)
        self.caps: list[float] = []

    def __call__(self, cap):  # the drafter factory: one drafter per call, with its cap
        self.caps.append(cap)
        return self

    def run(self, goal, cwd, permission_fn, stage="build", env=None):
        assert stage == "draft"
        self.requests.append(goal)
        doc = goal.split("docs/tasks/", 1)[1].split("/", 1)[1].split(".md", 1)[0]
        for path in self.reads:
            self.results.append((path, permission_fn("fs.read", path, [])))
        if doc in self.fail:
            return AgentResult("error", "stopped at the budget cap ($2.0)", self.cost)
        return AgentResult("done", self.docs[doc], self.cost)


def blocker(text="the steps are wrong", where="README.md:3"):
    return Review("fail", [Finding("blocker", where, text)], "nothing")


def junit_runner(results=None, exit_code=0):
    """A stand-in for running the plan's tests in the sandbox: writes a JUnit report.

    results: {file: (passed, failed)}; by default every plan test file passes 3 of 3."""
    calls = []

    def run(config, cwd, cmd, env):
        calls.append((cwd, cmd, env))
        files = results or {}
        if not files:
            files = {t.split(" ")[0]: (3, 0) for t in cmd.split("--junitxml=")[1].split(" ")[1:] if t.endswith(".py")}
        cases = []
        for f, (ok, bad) in files.items():
            mod = f[:-3].replace("/", ".")
            cases += [f'<testcase classname="{mod}" name="t{i}"/>' for i in range(ok)]
            cases += [f'<testcase classname="{mod}" name="f{i}"><failure message="x"/></testcase>' for i in range(bad)]
        junit = Path(cwd) / ".parallax-tmp" / "junit.xml"
        junit.write_text("<testsuites><testsuite>" + "".join(cases) + "</testsuite></testsuites>")
        return exit_code, f"{sum(v[0] for v in files.values())} passed"

    run.calls = calls
    return run


def good_probe(config, cwd, spec, env):
    return {"written": [], "readable": [], "network": [], "env": ["HOME", "PATH"], "env_values": []}
