"""The check, after every build: code first, then the plan's tests, then the blind checker.

1. Stage the worktree into a temporary index and record the tree hash (tree.py).
2. Code checks against the plan: files outside it, unlisted binaries or symlinks, dependencies,
   protected paths, the diff cap. Any of these sends the task to you, not to the checker. If you
   accept the risk (approve the item, with a reason), the next check of that same tree goes on.
3. Parallax runs the plan's tests itself, in the sandbox (testrun.py).
4. The blind checker gets exactly its brief (review.py). REVIEW.md decides which severities block.
5. Tests pass and nothing blocks: Ready. Otherwise the maker gets the findings and reworks, up to
   the rework cap (3). On re-review the checker gets its brief with the new diff, never the maker's
   reply. The fail after the last cycle, a checker error, or tests that can't run come to you.
"""
from __future__ import annotations

import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import build, costs, lifecycle, lint, review, testrun, tree
from .agents.base import BlindChecker, CheckerError, Review
from .core import ParallaxError, Project

CheckerFor = Callable[[float, str], BlindChecker]  # (budget left, model) -> checker


def _open_items(project: Project, task_id: str) -> list[dict]:
    return [e for e in project.inbox() if e["data"].get("task") == task_id]


def accepted_risk(project: Project, task_id: str, tree_hash: str) -> bool:
    """Did you accept the scope problems of this exact tree?"""
    raised = {e["id"]: e for e in project.ledger.entries()
              if e["kind"] == "disagreement.raised" and e["data"].get("task") == task_id
              and e["data"].get("stage") == "scope" and e["data"].get("tree") == tree_hash}
    return any(e["kind"] == "decision.resolved" and e["data"].get("decision") in raised
               and e["data"]["outcome"] == "approved" for e in project.ledger.entries())


def plan_wins(project: Project, task_id: str) -> bool:
    """Did you settle an intent-versus-plan conflict on this task in the plan's favour?"""
    raised = {e["id"] for e in project.ledger.entries()
              if e["kind"] == "disagreement.raised" and e["data"].get("task") == task_id
              and e["data"].get("stage") == "conflict"}
    return any(e["kind"] == "decision.resolved" and e["data"].get("decision") in raised
               and e["data"]["outcome"] == "approved" for e in project.ledger.entries())


def planned_paths(plan: dict) -> set[str]:
    return set(plan["files"]) | {t.split("::", 1)[0] for t in plan["tests"]}


def _path(where: str) -> str:
    return where.split(":", 1)[0].strip()


def rework_cycles(project: Project, task_id: str) -> int:
    return sum(e["kind"] == "rework.started" and e["data"].get("task") == task_id for e in project.ledger.entries())


def _to_you(project: Project, task_id: str, stage: str, why: str, **refs) -> str:
    project.ledger.append("disagreement.raised", "parallax", why, task=task_id, stage=stage, **refs)
    project.ledger.append("check.finished", "parallax", why, task=task_id, status="disputed", **refs)
    return "disputed"


def check_once(project: Project, task_id: str, checker_for: CheckerFor, test_runner=None) -> tuple[str, list[str]]:
    """One pass. Returns (status, what the maker should fix): status is ready, rework or disputed."""
    p = build.prepare(project, task_id, setup=False, launching=False)
    t, plan, settings = p.task, p.plan, project.policy.check
    project.ledger.append("check.started", "parallax", "", task=task_id)

    s = tree.conform(tree.stage(p.worktree, t["base"], p.home / "check.index"), plan, settings["diff_cap"])
    risk = bool(s.problems) and accepted_risk(project, task_id, s.tree)
    project.ledger.append("check.staged", "parallax", "", task=task_id, tree=s.tree, base=s.base,
                                   files=s.files, lines=s.lines, binaries=s.binaries, symlinks=s.symlinks,
                                   autorun=s.autorun, problems=s.problems, risk_accepted=risk)
    if s.problems and not risk:
        return _to_you(project, task_id, "scope", "; ".join(s.problems), tree=s.tree), []

    results, reset = testrun.run(p.worktree, t["base"], s.tree, plan, p.home, p.venv,
                                 build.scrubbed_env(p.venv), settings["test_command"], test_runner)
    project.ledger.append("tests.recorded", "parallax", results.tail, task=task_id, tree=s.tree,
                                  exit=results.exit, per_file=results.per_file, passed=results.passed,
                                  total=results.total, harness_reset=reset)
    if not results.ran:
        last = (results.tail.splitlines() or ["no output"])[-1]
        return _to_you(project, task_id, "check", f"the plan's tests couldn't run (exit {results.exit}): {last}",
                       tree=s.tree), []

    # code findings first: they go straight back to the maker, and the checker isn't paid to spot them
    dashes = [f"blocker {path}:{line}: an em dash was added; use a comma or a colon"
              for path, line, text in tree.added_lines(s.diff) if lint.EM_DASH in text]
    if dashes:
        project.ledger.append("check.found", "parallax", "; ".join(dashes), task=task_id, tree=s.tree, findings=dashes)
        return "rework", dashes + ([] if results.ok else [f"tests failed (exit {results.exit})"])

    intent = lifecycle.doc_path(project, task_id, "intent").read_text(encoding="utf-8")
    review_text = review.load(project.root)
    brief = review.brief(intent, review_text, plan["review_tightening"], s.diff)
    left = costs.budget(project, task_id, plan)[1]
    if left <= 0:
        project.ledger.append("check.finished", "parallax", "the budget cap is reached", task=task_id,
                              status="stuck", tree=s.tree)
        return costs.stop_at_cap(project, task_id, p.cap), []
    try:
        rv: Review = checker_for(left, settings["model"]).check(brief)
    except Exception as err:  # recorded for you, never retried silently
        project.ledger.append("verdict.recorded", "checker", str(err), task=task_id, stage="check", tree=s.tree,
                              verdict="error", findings=[], not_looked_at="everything: the checker failed",
                              brief_sha=hashlib.sha256(brief.encode()).hexdigest())
        return _to_you(project, task_id, "check", f"checker error: {err}", tree=s.tree), []

    blocking = review.blocking(review_text)
    blockers = [f for f in rv.findings if f.severity in blocking]
    # a scope finding on a file your approved plan lists: intent and plan disagree. that's yours,
    # never the maker's to settle, since fixing it would change approved scope
    against_plan = [f for f in blockers if f.kind == "scope" and _path(f.where) in planned_paths(plan)]
    if against_plan and plan_wins(project, task_id):
        blockers = [f for f in blockers if f not in against_plan]
        against_plan = []
    verdict = "fail" if blockers else ("pass" if rv.verdict == "pass" else "no_finding")
    project.ledger.append("verdict.recorded", "checker", "; ".join(f.text for f in rv.findings), task=task_id,
                          stage="check", tree=s.tree, verdict=verdict, checker_verdict=rv.verdict,
                          findings=[asdict(f) for f in rv.findings], not_looked_at=rv.not_looked_at,
                          model=rv.model or settings["model"], cost_usd=rv.cost_usd,
                          brief_sha=hashlib.sha256(brief.encode()).hexdigest())
    if against_plan:
        f = against_plan[0]
        return _to_you(project, task_id, "conflict",
                       f"intent and plan disagree: the checker says {f.where} goes against the intent "
                       f"({' '.join(f.text.split())}), but your approved plan lists {_path(f.where)}", tree=s.tree), []
    if results.ok and not blockers:
        project.ledger.append("check.finished", "parallax", "", task=task_id, status="ready", tree=s.tree)
        return "ready", []

    fix = [f"{f.severity} {f.where}: {f.text}".replace(" :", ":") for f in blockers]
    for f, (passed, counted, _) in results.per_file.items():
        if passed < counted:
            fix.append(f"tests: {f} has {counted - passed} of {counted} failing")
    if not results.ok and not any(line.startswith("tests:") for line in fix):
        fix.append(f"tests failed (exit {results.exit})")
    if not results.ok and results.tail:
        fix.append("test output, last lines:\n" + results.tail)
    return "rework", fix


def run_check(project: Project, task_id: str, checker_for: CheckerFor, maker_for, *,
              test_runner=None, preflight_runner=None) -> str:
    """Check, rework and re-check until Ready, or until it comes to you."""
    cap = project.policy.check["rework_cap"]
    while True:
        status, fix = check_once(project, task_id, checker_for, test_runner)
        if status != "rework":  # ready, disputed, or stuck at the cap
            return status
        cycles = rework_cycles(project, task_id)
        if cycles >= cap:
            summary = "; ".join(line.splitlines()[0] for line in fix)
            return _to_you(project, task_id, "check", f"the check still fails after {cap} rework cycles: {summary}")
        p = build.prepare(project, task_id, setup=False, launching=False)
        if p.left <= 0:
            return costs.stop_at_cap(project, task_id, p.cap)
        if not all(line.ok for line in build.run_preflight(project, p, preflight_runner)):
            project.ledger.append("stuck.raised", "parallax", "preflight failed before a rework, so it didn't launch",
                                  task=task_id)
            return "stuck"
        project.ledger.append("rework.started", "parallax", "\n".join(fix), task=task_id, cycle=cycles + 1)
        extra = ("The check found problems. Fix them, then stop:\n" + "\n".join(f"- {line}" for line in fix)
                 + "\n\nIf a finding can only be fixed by going against the approved plan (removing or not "
                   "doing something it lists), don't fix it: start your final reply with \"conflict:\" and "
                   "name the finding. That's for the human to decide.")
        before = project.ledger.entries()
        reviewed = [e for e in before if e["kind"] == "check.staged" and e["data"].get("task") == task_id][-1]["data"]["tree"]
        status = build.run_build(project, task_id, maker_for, extra=extra)
        if status != "built":
            return status
        wt = Path(project.task(task_id)["worktree"])
        had = set(tree.files_in(wt, reviewed))
        gone = sorted(f for f in planned_paths(p.plan) if f in had and not (wt / f).exists())
        if gone:  # the backstop: a rework may never drop what you approved
            return _to_you(project, task_id, "conflict",
                           f"the rework removed {', '.join(gone)}, which your approved plan lists")


def can_check(project: Project, task_id: str) -> None:
    if not any(e["kind"] == "build.finished" and e["data"].get("task") == task_id for e in project.ledger.entries()):
        raise ParallaxError(f"task {task_id} hasn't been built. run parallax build {task_id}")
    if _open_items(project, task_id):
        raise ParallaxError(f"task {task_id} has an item waiting on you. decide it in parallax inbox first")

