"""Reticle: tests of the intended outcome, written before the build, that Maker can't see or change.

Once per attempt, after the plan is approved and before Maker starts:
1. Reticle gets the intent's outcomes and constraints and a read-only copy of the base commit.
   Never the plan, the diff, or Maker's tests. It replies with one pytest file.
2. Parallax runs that file on the base commit, in the sandbox. A test counts only if it fails on an
   assertion there: it shows the problem the outcome names. A test that passes on the base, errors
   before its assertion, fails on another exception (an import, say), or names no outcome is
   dropped and recorded as weak.
3. The kept tests are stored under docs/tasks/<id>/reticle/, hashed in the ledger. Maker's
   worktree never holds them, and a changed hash stops the check.
At every check Parallax runs them on the reviewed tree. A failure goes back to Maker as a rework
finding naming the outcome and the assertion message, never the test's code.

Off by default ([reticle] in the policy). It joins the live loop only when the eval says it earns
its place: docs/evals.md.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

from . import build, costs, installs, lifecycle, lint, review, status, testrun, tree
from .agents.base import AgentResult
from .core import ROOT_ENV, TASK_ENV, Project
from .gate import make_permission_fn

FILE = "test_reticle.py"  # where the file sits in a tree when it runs: the root, so imports resolve as the repo's do
NAME = re.compile(r"^test_outcome_(\d+)(?:_|$)")
# a failure on an assertion, pytest.raises included. pytest writes a bare assert's message either as
# "assert x == y" or "AssertionError: assert x == y", depending on how it ran; both count
KEEPS = ("AssertionError", "assert ", "Failed: DID NOT RAISE")

REQUEST = """\
Write pytest tests for the outcomes below, against this repository as it is now.

Outcome:
{outcome}

Constraints:
{constraints}

Rules:
- Name every test test_outcome_<n>_<what>, where <n> is the number of the outcome it checks.
- Each test must fail on the code as it is now, on an assertion, because the outcome isn't met yet,
  and pass once it is. Test the behavior the outcome names, through the public interface.
- Import the code the way the repository's own tests do. No new dependencies, no network, no files
  outside a temporary folder.
- If an outcome can't be tested this way, leave it out; don't write a test that passes now.
Your final reply is the test file's full text and nothing else."""


def settings(project: Project) -> dict:
    cfg = dict(project.policy.reticle)
    cfg["model"] = cfg["model"] or project.policy.draft["model"]
    return cfg


def recorded(project: Project, task_id: str) -> dict | None:
    """This attempt's Reticle run, if it has had one."""
    hits = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] in ("reticle.recorded", "reticle.failed")]
    return hits[-1] if hits else None


def kept(project: Project, task_id: str) -> list[dict]:
    rec = recorded(project, task_id)
    return list(rec["data"].get("kept") or []) if rec and rec["kind"] == "reticle.recorded" else []


def stored(project: Project, task_id: str) -> Path:
    return lifecycle.task_dir(project, task_id) / "reticle" / FILE


def request(intent: str) -> str:
    return REQUEST.format(outcome=review.section(intent, "Outcome"), constraints=review.section(intent, "Constraints"))


def _unfence(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```[\w-]*\n(.*?)\n?```\s*$", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def judge(cases: list[dict], outcomes: list[str]) -> tuple[list[dict], list[dict]]:
    """(kept, weak) from the tests' run on the base commit."""
    keep, weak = [], []
    for c in cases:
        if c["file"] != FILE and c["outcome"] != "error":  # a collection error names no class, so no file: it's ours
            continue
        m = NAME.match(c["name"])
        if c["outcome"] == "error" and not m:
            weak.append({"name": c["name"], "why": f"the file doesn't load on the base ({c['message'] or 'collection error'})"})
        elif not m or m.group(1) not in outcomes:
            weak.append({"name": c["name"], "why": "it names no outcome in the intent"})
        elif c["outcome"] == "pass":
            weak.append({"name": c["name"], "why": "it passes on the base, so it doesn't show the problem"})
        elif c["outcome"] == "error":
            weak.append({"name": c["name"], "why": f"it errors before its assertion: {c['message']}"})
        elif c["outcome"] == "skip":
            weak.append({"name": c["name"], "why": "it skips on the base"})
        elif not c["message"].startswith(KEEPS):
            kind = c["message"].split(":", 1)[0] or "an error"
            weak.append({"name": c["name"], "why": f"it fails on {kind}, not an assertion"})
        else:
            keep.append({"node": f"{FILE}::{c['name']}", "name": c["name"], "outcome": m.group(1),
                         "base_message": c["message"]})
    return keep, weak


def _run(project: Project, p, treeish: str, text: bytes, nodes: list[str], where: str, runner=None) -> testrun.Results:
    plan = {"tests": nodes, "outside_reads": [], "domains": []}
    results, _ = testrun.run(p.worktree, p.task["base"], treeish, plan, p.home / where, p.venv, build.scrubbed_env(p.venv),
                             project.policy.check["test_command"], runner, overlay={FILE: text})
    return results


def write(project: Project, task_id: str, p, writer=None, runner=None) -> str:
    """Once per attempt, before Maker. Returns "kept", "none" (no test was worth keeping), "failed",
    "done" (already ran this attempt) or "off". Never stops the task: without tests, it builds as before."""
    cfg = settings(project)
    if not cfg["enabled"]:
        return "off"
    if recorded(project, task_id):
        return "done"
    left = costs.budget(project, task_id, p.plan)[1]
    limit = round(min(float(cfg["max_usd"]), left), 2)
    if limit <= 0:
        project.ledger.append("reticle.failed", "parallax", "no budget left for Reticle", task=task_id)
        return "failed"
    base = p.home / "reticle" / "base"  # the base commit, never the worktree: it may hold earlier work
    shutil.rmtree(base.parent, ignore_errors=True)
    tree.export(p.worktree, p.task["base"], base)
    installs.place(p.venv, base)
    intent = lifecycle._read(project, task_id, "intent")
    fn = make_permission_fn(project, task_id, base, read_only=True)
    try:
        res = (writer or WRITER)(limit, cfg["model"]).run(request(intent), base, fn, stage="reticle",
                                                          env={TASK_ENV: task_id, ROOT_ENV: str(project.root)})
    except Exception as err:  # recorded, never retried silently; the build goes on without it
        res = AgentResult("error", f"{type(err).__name__}: {err}")
    common = dict(task=task_id, cost_usd=res.cost_usd, model=cfg["model"])
    if res.status != "done" or not (res.summary or "").strip():
        project.ledger.append("reticle.failed", "reticle", lint.one_sentence(res.summary or res.status), **common)
        return "failed"
    text = _unfence(res.summary).encode()
    results = _run(project, p, p.task["base"], text, [FILE], "reticle-base", runner)
    keep, weak = judge(results.cases, lint.outcomes_of(intent))
    if not results.cases:
        weak.append({"name": FILE, "why": f"its tests couldn't run on the base (exit {results.exit}): "
                                          f"{(results.tail.splitlines() or ['no output'])[-1][:200]}"})
    path = stored(project, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text)
    rel = path.relative_to(project.root).as_posix()
    project.ledger.append("reticle.recorded", "reticle",
                          lint.one_sentence(f"{len(keep)} tests kept, {len(weak)} weak ones dropped"),
                          file=rel, sha=hashlib.sha256(text).hexdigest(), kept=keep, weak=weak, **common)
    return "kept" if keep else "none"


def tampered(project: Project, task_id: str) -> bool:
    rec = recorded(project, task_id)
    if not rec or rec["kind"] != "reticle.recorded":
        return False
    path = project.root / rec["data"]["file"]
    return not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != rec["data"]["sha"]


def check(project: Project, task_id: str, p, treeish: str, runner=None) -> tuple[testrun.Results, list[dict]] | None:
    """Run the kept tests on a reviewed tree. None if there are none. Returns (results, failing kept tests)."""
    tests = kept(project, task_id)
    if not tests:
        return None
    text = stored(project, task_id).read_bytes()
    results = _run(project, p, treeish, text, [t["node"] for t in tests], "reticle-check", runner)
    by_name = {c["name"]: c for c in results.cases if c["file"] == FILE}
    failing = [{**t, "message": by_name.get(t["name"], {}).get("message") or "it didn't run"}
               for t in tests if by_name.get(t["name"], {}).get("outcome") != "pass"]
    return results, failing


def finding(t: dict) -> str:
    """What Maker hears: the outcome and the assertion message, never the test's code."""
    return f"blocker: a test of outcome {t['outcome']} that you can't see fails: {t['message']}"


def _default_writer(limit: float, model: str):
    from .agents.claude import ClaudeAgent
    return ClaudeAgent(model=model, max_budget_usd=limit)


WRITER = _default_writer  # tests replace this; they never call a model
