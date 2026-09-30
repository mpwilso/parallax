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

from . import build, costs, lifecycle, lint, reticle, review, sandbox, status, testrun, tree, uitest
from .agents.base import BlindChecker, Review
from .core import ParallaxError, Project

REWORK = """The check found problems. Fix them, then stop:
{fixes}

If a finding can only be fixed by going against the approved plan (removing or not doing something \
it lists), don't fix it: start your final reply with "conflict:" and name the finding. That's for the \
human to decide."""

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


def _staged(project: Project, task_id: str, p, plan: dict, settings: dict) -> tree.Staged:
    """Stage the worktree and check it against the plan. The UI tester's tests count as planned."""
    return tree.conform(tree.stage(p.worktree, p.task["base"], p.home / "check.index"), plan, settings["diff_cap"])


def _still_failing(project: Project, task_id: str, flows) -> list[dict]:
    """The flow tests failing now that failed at the last check too, with a rework between."""
    runs = [e for e in status.attempt(project.ledger.entries(), task_id) if e["kind"] == "flows.recorded"]
    if len(runs) < 2:
        return []
    before = {(Path(c["file"]).name, c["name"]) for c in runs[-2]["data"].get("failed") or []}
    return [c for c in flows.failed if (Path(c["file"]).name, c["name"]) in before]


def _retried_checker(project: Project, task_id: str) -> bool:
    return any(e["kind"] == "check.retried" for e in status.attempt(project.ledger.entries(), task_id))


def rework_cycles(project: Project, task_id: str) -> int:
    """Rework cycles in this attempt."""
    return sum(e["kind"] == "rework.started" for e in status.attempt(project.ledger.entries(), task_id))


def _to_you(project: Project, task_id: str, stage: str, why: str, **refs) -> str:
    project.ledger.append("disagreement.raised", "parallax", why, task=task_id, stage=stage, **refs)
    project.ledger.append("check.finished", "parallax", why, task=task_id, status="disputed", **refs)
    return "disputed"


def check_once(project: Project, task_id: str, checker_for: CheckerFor, test_runner=None) -> tuple[str, list[str]]:
    """One pass. Returns (status, what the maker should fix): status is ready, rework or disputed."""
    p = build.prepare(project, task_id, setup=False, launching=False)
    t, plan, settings = p.task, p.plan, project.policy.check
    project.ledger.append("check.started", "parallax", "", task=task_id)

    removed = sandbox.remove_leftovers(p.worktree, set(plan["files"]))  # every placeholder, whenever it appeared (bb4040)
    if removed:
        project.ledger.append("sandbox.cleaned", "parallax", "removed the sandbox's empty placeholder files before the check",
                              task=task_id, files=removed)
    s = _staged(project, task_id, p, plan, settings)
    empty = [path for cause, path in s.issues if cause == "outside" and tree._size(p.worktree, path) == 0]
    if empty:  # an empty file outside the plan is a placeholder, not work: removed, recorded, never a decision
        sandbox.remove_files(p.worktree, empty)
        project.ledger.append("sandbox.cleaned", "parallax", "removed empty files outside the plan's files",
                              task=task_id, files=empty)
        s = _staged(project, task_id, p, plan, settings)
    if not s.problems and uitest.applies(project, plan) and not uitest.recorded(project, task_id):
        if costs.budget(project, task_id, plan)[1] <= 0:
            return costs.stop_at_cap(project, task_id, p.cap, "Field was about to use the app"), []
        try:  # the UI tester, once per attempt, before anything else is paid for
            outcome, why = uitest.test(project, task_id, p, uitest.TESTER)
        except uitest.UITestError as err:
            return _to_you(project, task_id, "check", f"Field (the UI tester) couldn't run: {err}", tree=s.tree), []
        if costs.budget(project, task_id, plan)[1] <= 0:  # its spend reached the cap: nothing more runs
            return costs.stop_at_cap(project, task_id, p.cap, "Field was using the app"), []
        if outcome == "app":
            project.ledger.append("check.found", "parallax", why, task=task_id, tree=s.tree, findings=[f"blocker: {why}"])
            return "rework", [f"blocker: {why}"]
        if outcome == "you":
            return _to_you(project, task_id, "check", why, tree=s.tree), []
    if reticle.tampered(project, task_id):
        return _to_you(project, task_id, "guard", "Reticle's tests changed after they were recorded", tree=s.tree), []
    changed = uitest.tampered(project, task_id)
    if changed:
        return _to_you(project, task_id, "guard", f"Field's tests changed after it wrote them: {', '.join(changed)}",
                       tree=s.tree), []
    risk = bool(s.problems) and accepted_risk(project, task_id, s.tree)
    project.ledger.append("check.staged", "parallax", "", task=task_id, tree=s.tree, base=s.base,
                                   files=s.files, lines=s.lines, binaries=s.binaries, symlinks=s.symlinks,
                                   autorun=s.autorun, problems=s.problems, risk_accepted=risk)
    if s.problems and not risk:
        why, files = tree.describe(s, p.worktree)
        return _to_you(project, task_id, "scope", why, tree=s.tree, files=files), []

    results, reset = testrun.run(p.worktree, t["base"], s.tree, plan, p.home, p.venv,
                                 build.scrubbed_env(p.venv), settings["test_command"], test_runner)
    project.ledger.append("tests.recorded", "parallax", results.tail, task=task_id, tree=s.tree,
                                  exit=results.exit, per_file=results.per_file, passed=results.passed,
                                  total=results.total, harness_reset=reset)
    if not results.ran:
        missing = sorted({x.split("::", 1)[0] for x in plan["tests"] if not (p.worktree / x.split("::", 1)[0]).exists()})
        if missing:  # a planned test file that isn't there conflicts with your plan; it isn't a test setup problem
            return _to_you(project, task_id, "conflict", f"the plan's test file {missing[0]} is missing from the change, "
                           f"but your approved plan lists it", tree=s.tree, missing=missing), []
        last = (results.tail.splitlines() or ["no output"])[-1]
        return _to_you(project, task_id, "check", f"the plan's tests couldn't run (exit {results.exit}): {last}",
                       tree=s.tree), []
    try:
        flows = uitest.run_flows(project, task_id, p, s.tree, uitest.FLOW_RUNNER)
    except uitest.UITestError as err:
        return _to_you(project, task_id, "check", f"the UI flow tests couldn't run: {err}", tree=s.tree), []
    flow_fix = []
    if flows is not None:
        project.ledger.append("flows.recorded", "parallax", flows.tail, task=task_id, tree=s.tree, ran=flows.ran,
                              app_failed=flows.app_failed, passed=len(flows.cases) - len(flows.failed),
                              total=len(flows.cases), failed=flows.failed)
        if flows.app_failed:
            flow_fix = [f"blocker: the app didn't start for the UI flow tests:\n{flows.tail}"]
        elif not flows.ran:
            return _to_you(project, task_id, "check", f"the UI flow tests couldn't run: {lint.one_sentence(flows.tail or 'no report')}",
                           tree=s.tree), []
        else:
            flow_fix = [f"blocker {c['file']}: the UI flow \"{c['name']}\" fails: {c['message']}" for c in flows.failed]
            still = _still_failing(project, task_id, flows)
            if still:  # after a rework the test still fails: it or the app is wrong, and only you can say which
                c = still[0]
                files = sorted({g for g in uitest.guarded(project, task_id) for x in still if Path(g).name == Path(x["file"]).name})
                return _to_you(project, task_id, "flows",
                               f"Field's test \"{c['name']}\" ({Path(c['file']).name}) still fails after a rework: "
                               f"{c['message']}. Either the test or the app is wrong", tree=s.tree, files=files), []

    # Reticle's tests of the outcomes, on this tree. A failure goes back to Maker as a finding; Second
    # Eye still judges the tree, so the eval can tell the two apart
    try:
        ran = reticle.check(project, task_id, p, s.tree, test_runner)
    except reticle.Unrun as err:  # never a finding for Maker: nothing it did made them not run
        return _to_you(project, task_id, "check", f"Reticle's tests couldn't run: {err}", tree=s.tree), []
    if ran is not None:
        r_results, failing = ran
        project.ledger.append("reticle.ran", "parallax", r_results.tail, task=task_id, tree=s.tree, exit=r_results.exit,
                              passed=len(reticle.kept(project, task_id)) - len(failing), total=len(reticle.kept(project, task_id)),
                              failed=[{k: t[k] for k in ("name", "outcome", "message")} for t in failing])
        if not r_results.reported:
            return _to_you(project, task_id, "check", f"Reticle's tests couldn't run (exit {r_results.exit}): "
                           f"{lint.one_sentence((r_results.tail.splitlines() or ['no output'])[-1])}", tree=s.tree), []
        flow_fix = flow_fix + [reticle.finding(t) for t in failing]

    # code findings first: they go straight back to the maker, and the checker isn't paid to spot them.
    # The em dash rule is Parallax's own style: only a repo whose policy turns it on gets it
    dashes = [f"blocker {path}:{line}: an em dash was added; use a comma or a colon"
              for path, line, text in tree.added_lines(s.diff) if lint.EM_DASH in text] if settings["no_em_dashes"] else []
    if dashes:
        project.ledger.append("check.found", "parallax", "; ".join(dashes), task=task_id, tree=s.tree, findings=dashes)
        return "rework", dashes + flow_fix + ([] if results.ok else [f"tests failed (exit {results.exit})"])

    intent = lifecycle.doc_path(project, task_id, "intent").read_text(encoding="utf-8")
    review_text = review.load(project.root)
    brief = review.brief(intent, review_text, plan["review_tightening"], s.diff)
    left = costs.budget(project, task_id, plan)[1]
    if left <= 0:
        project.ledger.append("check.finished", "parallax", "the budget cap is reached", task=task_id,
                              status="stuck", tree=s.tree)
        return costs.stop_at_cap(project, task_id, p.cap), []
    rv: Review | None = None
    for attempt_no in (1, 2):  # a garbled reply is asked once more, recorded; a second one comes to you
        try:
            rv = checker_for(left, settings["model"]).check(brief)
            break
        except Exception as err:
            project.ledger.append("verdict.recorded", "checker", str(err), task=task_id, stage="check", tree=s.tree,
                                  verdict="error", findings=[], not_looked_at="everything: Second Eye failed",
                                  brief_sha=hashlib.sha256(brief.encode()).hexdigest())
            if attempt_no == 1 and not _retried_checker(project, task_id):
                project.ledger.append("check.retried", "parallax",
                                      f"Second Eye gave no usable verdict ({err}), so it was asked once more", task=task_id)
                continue
            return _to_you(project, task_id, "check", f"Second Eye error: {err}", tree=s.tree), []

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
                       f"intent and plan disagree: Second Eye says {f.where} goes against the intent "
                       f"({' '.join(f.text.split())}), but your approved plan lists {_path(f.where)}", tree=s.tree), []
    if results.ok and not blockers and not flow_fix:
        project.ledger.append("check.finished", "parallax", "", task=task_id, status="ready", tree=s.tree)
        return "ready", []

    fix = [f"{f.severity} {f.where}: {f.text}".replace(" :", ":") for f in blockers] + flow_fix
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
        project.ledger.append("rework.started", "parallax", "\n".join(fix), task=task_id, cycle=cycles + 1)
        extra = REWORK.format(fixes="\n".join(f"- {line}" for line in fix))
        before = project.ledger.entries()
        reviewed = [e for e in before if e["kind"] == "check.staged" and e["data"].get("task") == task_id][-1]["data"]["tree"]
        status = build.run_build(project, task_id, maker_for, extra=extra, preflight_runner=preflight_runner)
        if status != "built":
            return status
        wt = Path(project.task(task_id)["worktree"])
        had = set(tree.files_in(wt, reviewed))
        gone = sorted(f for f in planned_paths(p.plan) if f in had and not (wt / f).exists())
        if gone:  # the backstop: a rework may never drop what you approved
            return _to_you(project, task_id, "conflict",
                           f"the rework removed {', '.join(gone)}, which your approved plan lists")


def summarize(fix: list[str]) -> str:
    """What a rework is fixing, in one clause: failing test files by name, then the findings."""
    files = [line.split(":", 1)[1].split(" has ", 1)[0].strip().rsplit("/", 1)[-1].removesuffix(".py")
             for line in fix if line.startswith("tests:") and " has " in line]
    other = [line.splitlines()[0] for line in fix if not line.startswith(("tests:", "test output"))]
    parts = []
    if files:
        parts.append(f"failing tests in {', '.join(files[:4])}" + (f" and {len(files) - 4} more" if len(files) > 4 else ""))
    if other:
        parts.append(other[0] + (f" and {len(other) - 1} more findings" if len(other) > 1 else ""))
    return "; ".join(parts) or "the check's findings"


def can_check(project: Project, task_id: str) -> None:
    if not any(e["kind"] == "build.finished" and e["data"].get("task") == task_id for e in project.ledger.entries()):
        raise ParallaxError(f"task {task_id} hasn't been built. run parallax build {task_id}")
    if _open_items(project, task_id):
        raise ParallaxError(f"task {task_id} has an item waiting on you. decide it in parallax inbox first")

