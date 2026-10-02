"""`parallax eval`: real merged fixes, run through `parallax do`'s pipeline, scored by the maintainers' tests.

Per case: a scratch clone of the upstream repo at the base commit, with no history after it, so
the fix can't be found. Its policy is yours, with the case's setup, the UI tester off, and the
per-case ceiling as both the small-task cap and the launch rule. Its REVIEW.md is the general
template `parallax init` gives any repo. The issue text goes to
`pilot.intake` exactly as `parallax do` would take it, and the pilot runs to Ready or to one
Decision needed. Nothing answers a decision; the case ends there.

The hidden tests (the files the maintainers' fix added or changed) stay in the upstream cache,
which no agent is given and the sandbox can't read. After the pipeline ends, Parallax puts them
over each tree Second Eye judged and runs them itself, in the sandbox (testrun.py).

`parallax eval check` runs no model: the hidden tests must fail at the base and pass at the fix.
See docs/plan.md, "Evals", for the design.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import time
import tomllib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import build, fingerprint, installs, lifecycle, lint, pilot, sandbox, status, testrun, tree
from .core import POLICY_FILE, ParallaxError, Project, git_failed

CASES_FILE = Path("evals") / "cases.toml"
RESULTS_DIR = Path("evals") / "results"
MARGIN = 0.10          # of a case's ceiling, kept back for a turn in flight past its cap
DRAFT_SHARE = 6        # a drafting call may spend at most the ceiling over this: 3 tries of intent and plan
CASE_TIMEOUT = 2 * 3600
PYTHON = "/usr/bin/python3"  # the sandbox can read it; a uv-managed Python lives under $HOME, which it can't
DECISIONS = ("stuck.raised", "disagreement.raised", "review.requested")

Pipeline = Callable[[Project, str], str]  # (the case's scratch project, the work as typed) -> task id


@dataclass
class Case:
    id: str
    repo: str
    base: str
    fix: str
    tests: list[str]
    goal: str
    test_command: str = "python -m pytest -q"  # the SWE-bench field; Parallax runs its own, with a report
    setup: list[str] = field(default_factory=lambda: ["uv pip install -e . pytest"])
    pr: str = ""
    issue: str = ""


def home() -> Path:
    return sandbox.data_home() / "evals"


def load_cases(root: Path, only: list[str] | None = None) -> list[Case]:
    path = Path(root) / CASES_FILE
    if not path.is_file():
        raise ParallaxError(f"no {CASES_FILE} here. run this from the parallax repo folder")
    with open(path, "rb") as f:
        cases = [Case(**c) for c in tomllib.load(f).get("case", [])]
    if only:
        missing = sorted(set(only) - {c.id for c in cases})
        if missing:
            raise ParallaxError(f"no case {', '.join(missing)} in {CASES_FILE}")
        by_id = {c.id: c for c in cases}
        cases = [by_id[i] for i in dict.fromkeys(only)]  # in the order you named them: the budget reaches the first
    return cases


# plumbing -------------------------------------------------------------------------------------

def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise git_failed(args, out.stderr or out.stdout)
    return out.stdout.strip()


def cache_repo(case: Case) -> Path:
    """A full clone of the upstream repo, fetched once and reused. No agent is ever given it."""
    name = case.repo.rstrip("/").split("/")[-1].removesuffix(".git") + "-" + uuid.uuid5(uuid.NAMESPACE_URL, case.repo).hex[:6]
    path = home() / "cache" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _git(path.parent, "clone", "--quiet", case.repo, str(path))
    elif subprocess.run(["git", "cat-file", "-e", f"{case.fix}^{{commit}}"], cwd=path, capture_output=True).returncode:
        _git(path, "fetch", "--quiet", "origin")
    return path


def clone_at(cache: Path, commit: str, dest: Path) -> None:
    """A fresh repo at commit, holding no history after it: the fix isn't in it to be found."""
    branch = f"parallax-eval/{commit[:12]}"
    _git(cache, "branch", "--force", branch, commit)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _git(dest.parent, "clone", "--quiet", "--no-local", "--single-branch", "--no-tags", "--branch", branch,
         str(cache), str(dest))


def hidden_tests(case: Case, cache: Path) -> dict[str, bytes]:
    """The maintainers' test files, as of the fix."""
    return {rel: tree.show_file(cache, case.fix, rel) for rel in case.tests}


def setup_command(case: Case) -> str:
    """The case's setup as a [build] setup command: a venv at $PARALLAX_VENV, made as you."""
    if not case.setup:
        return ""
    steps = " && ".join(case.setup)
    return f'uv venv -q --python {PYTHON} "$PARALLAX_VENV" && export VIRTUAL_ENV="$PARALLAX_VENV" UV_LINK_MODE=copy && {steps}'


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value)  # strings and lists of strings: JSON's forms are valid TOML


def eval_policy(policy, case: Case, ceiling: float, reticle: bool | None = None) -> str:
    """Your policy, with the case's setup, the UI tester off, and the ceiling as cap and launch rule.
    reticle: Reticle on or off; None follows your policy, with your model and limit for it."""
    tables = {
        "limits": policy.limits,
        "budget": {"drafting_usd": round(min(policy.budget["drafting_usd"], ceiling / DRAFT_SHARE), 2),
                   "small_cap_usd": ceiling, "large_cap_usd": ceiling,
                   "small_floor_usd": policy.budget["small_floor_usd"], "large_floor_usd": policy.budget["large_floor_usd"]},
        "launch": {"auto_launch_usd": ceiling, "review_paths": [], "review_plans": False},
        "build": {"setup": setup_command(case)},
        "draft": policy.draft,
        "check": {**policy.check, "no_em_dashes": False},  # Parallax's own style rule, not the case's
        "ui_tester": {"enabled": False},
        "reticle": {**policy.reticle, "enabled": policy.reticle["enabled"] if reticle is None else reticle},
    }
    lines = [f"# written by parallax eval for case {case.id}: your policy, run hands-free under a ${ceiling:.2f} ceiling"]
    for name, values in tables.items():
        lines += ["", f"[{name}]", *[f"{k} = {_toml(v)}" for k, v in values.items()]]
    return "\n".join(lines) + "\n"


def run_hidden(repo: Path, base: str, treeish: str, hidden: dict[str, bytes], tests: list[str], home_: Path,
               venv: Path | None, command: str, runner=None) -> testrun.Results:
    """The hidden tests over a tree, run by Parallax in the sandbox, the way the check runs a plan's tests."""
    home_.mkdir(parents=True, exist_ok=True)
    plan = {"tests": tests, "outside_reads": [], "domains": []}
    results, _ = testrun.run(repo, base, treeish, plan, home_, venv, build.scrubbed_env(venv), command, runner,
                             overlay=hidden)
    return results


def _venv(case: Case, repo: Path, where: Path) -> Path | None:
    """For eval check: the case's setup, made exactly the way a task's is (installs.py)."""
    command = setup_command(case)
    if not command:
        return None
    out, _ = installs.make(command, repo, case.base, where / "venv", where / "setup-base")
    if out.returncode != 0:
        lines = (out.stderr or out.stdout).strip().splitlines() or ["no output"]
        raise ParallaxError(f"setup failed: {next((x.strip() for x in lines if 'error' in x.lower()), lines[-1])[:200]}")
    return where / "venv"


# eval check: no model ------------------------------------------------------------------------------

def check_case(case: Case, command: str, runner=None) -> tuple[bool, str]:
    """(sound, what was seen). Sound: the hidden tests fail at the base and pass at the fix."""
    cache = cache_repo(case)
    dest = home() / "check" / case.id
    if dest.exists():
        shutil.rmtree(dest)  # this case's own check folder, nothing else
    dest.mkdir(parents=True)
    venv = _venv(case, cache, dest)  # the cache is only read here: git archive and show, no checkout
    hidden = hidden_tests(case, cache)
    before = run_hidden(cache, case.base, case.base, hidden, case.tests, dest / "base", venv, command, runner)
    after = run_hidden(cache, case.base, case.fix, {}, case.tests, dest / "fix", venv, command, runner)
    at = lambda r: f"{r.passed} of {r.total} pass"  # noqa: E731
    if not before.reported:
        return False, f"the hidden tests couldn't run at the base (exit {before.exit}): {_last(before.tail)}"
    if not before.ran:  # an import error at the base looks the same whether the bug or the setup caused it
        return False, f"the hidden tests error at the base before any test runs (exit {before.exit}), so they don't show the bug"
    if before.ok:
        return False, f"the hidden tests already pass at the base ({at(before)})"
    if not after.ran:
        return False, f"the hidden tests couldn't run at the fix (exit {after.exit}): {_last(after.tail)}"
    if not after.ok:
        return False, f"the hidden tests fail even with the fix ({at(after)})"
    return True, f"base {at(before)}, fix {at(after)}"


def _last(tail: str) -> str:
    return (tail.strip().splitlines() or ["no output"])[-1][:200]


# the run ---------------------------------------------------------------------------------------------

def live_pipeline(project: Project, work: str) -> str:
    """`parallax do`, waited on: intake, then the detached pilot with the scrubbed environment."""
    started: list[tuple[subprocess.Popen, object]] = []

    def spawn(argv, env, cwd, log):
        out = open(log, "ab")
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True)
        started.append((proc, out))
        return proc.pid

    task_id = pilot.intake(project, work, spawn=spawn)["task"]
    proc, out = started[0]
    try:
        proc.wait(timeout=CASE_TIMEOUT)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait()
        project.ledger.append("stuck.raised", "parallax", f"the eval stopped it after {CASE_TIMEOUT // 60} minutes",
                              task=task_id)
    finally:
        out.close()
    return task_id


def _judge(verdict: str, hidden_pass: bool) -> str:
    if verdict == "fail":
        return "false alarm" if hidden_pass else "catch"
    return "right" if hidden_pass else "miss"


def _seconds(a: str, b: str) -> float:
    return round((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds(), 1)


def score(project: Project, task_id: str, case: Case, cache: Path, runner=None) -> dict:
    """Everything a case is scored on, from the scratch ledger and the hidden tests."""
    t = project.task(task_id)
    wt, base = Path(t["worktree"]), t["base"]
    mine = [e for e in project.ledger.entries() if e["data"].get("task") == task_id]
    judged: dict[str, str] = {}  # tree -> Second Eye's last verdict on it
    for e in mine:
        if e["kind"] == "verdict.recorded" and e["data"].get("verdict") != "error":
            judged[e["data"]["tree"]] = e["data"]["verdict"]
    staged = [e["data"]["tree"] for e in mine if e["kind"] == "check.staged"]
    tested = {e["data"]["tree"]: "fail" if e["data"]["failed"] else "pass" for e in mine if e["kind"] == "reticle.ran"}
    work = sandbox.task_home(project.root, task_id)
    work.mkdir(parents=True, exist_ok=True)
    final = staged[-1] if staged else tree.stage(wt, base, work / "eval.index").tree
    venv = work / "venv" if (work / "venv").exists() else None
    hidden = hidden_tests(case, cache)
    command = project.policy.check["test_command"]
    runs = {}
    for n, treeish in enumerate(dict.fromkeys([*judged, *tested, final])):
        runs[treeish] = run_hidden(wt, base, treeish, hidden, case.tests, work / f"eval-{n}", venv, command, runner)
    verdicts = [{"tree": tr, "verdict": v, "hidden": "pass" if runs[tr].ok else "fail",
                 "judgment": _judge(v, runs[tr].ok)} for tr, v in judged.items()]
    reticle_verdicts = [{"tree": tr, "verdict": v, "hidden": "pass" if runs[tr].ok else "fail",
                         "judgment": _judge(v, runs[tr].ok)} for tr, v in tested.items()]
    last = runs[final]
    ready = next((e["ts"] for e in mine if e["kind"] == "check.finished" and e["data"].get("status") == "ready"), None)
    raised = [e["reason"] for e in mine if e["kind"] in DECISIONS]
    return {
        "task": task_id,
        "end": status.board(t["status"]),
        "status": t["status"],
        "hidden": ("pass" if last.ok else "fail") if last.reported else "not run",
        "hidden_detail": f"{last.passed} of {last.total} pass (exit {last.exit})",
        "second_eye": next((v["judgment"] for v in verdicts if v["tree"] == final), "did not judge the final tree"),
        "verdicts": verdicts,
        "cost_usd": round(sum(e["data"].get("cost_usd") or 0 for e in mine), 4),
        "seconds_to_ready": _seconds(mine[0]["ts"], ready) if ready else None,
        "touches": len(raised) + 1,  # each Decision needed, plus the final accept or reject
        "decisions": [lint.one_sentence(r)[:300] for r in raised],
        "inferred": sorted(n for n, k in lint.outcome_kinds(lifecycle._read(project, task_id, "intent")).items()
                           if k == "inferred"),
        **_reticle_score(mine, reticle_verdicts, final),
    }


def _reticle_score(mine: list[dict], verdicts: list[dict], final: str) -> dict:
    """Reticle's part: its verdict on each tree it tested against the hidden tests, what it cost, and
    the Maker rework its false alarms caused, which counts against it."""
    rec = next((e for e in reversed(mine) if e["kind"] in ("reticle.recorded", "reticle.failed")), None)
    if rec is None:
        return {}
    judged = {v["tree"]: v["judgment"] for v in verdicts}
    alarm_usd, tree_, owed = 0.0, None, False
    for e in mine:  # a rework sent for a Reticle false alarm: the Maker run that follows is its cost
        if e["kind"] == "check.staged":
            tree_ = e["data"]["tree"]
        elif e["kind"] == "rework.started":
            owed = "that you can't see fails" in e["reason"] and judged.get(tree_) == "false alarm"
        elif e["kind"] == "maker.finished" and owed:
            alarm_usd += e["data"].get("cost_usd") or 0
            owed = False
    kept = rec["data"].get("kept") or []
    if rec["kind"] == "reticle.failed":
        verdict = "failed to write tests"
    elif not kept:
        verdict = "no test kept"
    else:
        verdict = judged.get(final, "did not test the final tree")
    return {
        "reticle": verdict,
        "reticle_verdicts": verdicts,
        "reticle_kept": len(kept),
        "reticle_weak": [w["why"] for w in rec["data"].get("weak") or []],
        "reticle_cost_usd": round(sum(e["data"].get("cost_usd") or 0 for e in mine if e["kind"].startswith("reticle.")), 4),
        "reticle_false_alarm_rework_usd": round(alarm_usd, 4),
    }


def run_case(case: Case, where: Path, ceiling: float, policy, pipeline: Pipeline, runner=None,
             reticle: bool | None = None) -> dict:
    started = time.monotonic()
    r: dict = {"case": case.id, "pr": case.pr, "issue": case.issue, "ceiling_usd": ceiling}
    try:
        cache = cache_repo(case)
        repo = where / "repo"
        clone_at(cache, case.base, repo)
        (repo / POLICY_FILE).write_text(eval_policy(policy, case, ceiling, reticle), encoding="utf-8")
        project = Project.init(repo, actor="parallax")  # REVIEW.md: the general template, as any repo gets
        task_id = pipeline(project, case.goal.strip())
        r.update(score(project, task_id, case, cache, runner))
        r["scratch"] = str(repo)
    except Exception as err:  # a crashed case is a result too, never skipped quietly
        r.update({"end": "error", "error": f"{type(err).__name__}: {err}"[:300]})
        r.setdefault("cost_usd", 0.0)
    r["seconds"] = round(time.monotonic() - started, 1)
    return r


def _write(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def say_now(text: str) -> None:
    """A progress line, out at once even when output goes to a file or a pipe."""
    print(text, flush=True)


def run(project: Project, cases: list[Case], budget: float, per_case: float | None = None,
        pipeline: Pipeline = live_pipeline, runner=None, say: Callable[[str], None] | None = None,
        run_id: str | None = None, reticle: bool | None = None) -> Path:
    """Run cases in order under a hard total budget. Each case starts only if what's spent, plus its
    ceiling and the margin, fits. Every case's result is written as it finishes. Returns the run's folder."""
    say = say or say_now
    reticle = project.policy.reticle["enabled"] if reticle is None else reticle  # your policy's, unless told
    per_case = float(per_case or project.policy.budget["small_cap_usd"])
    need = round(per_case * (1 + MARGIN), 2)
    run_id = run_id or uuid.uuid4().hex[:6]
    now = datetime.now(timezone.utc)
    out = project.root / RESULTS_DIR / f"{now:%Y-%m-%d}-{run_id}"
    out.mkdir(parents=True, exist_ok=True)
    scratch = home() / "runs" / run_id
    head = subprocess.run(["git", "-C", str(project.root), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    header = {"run": run_id, "started": now.isoformat(timespec="seconds"), "parallax": head,
              "budget_usd": budget, "per_case_usd": per_case, "cases": [c.id for c in cases],
              "fingerprint": fingerprint.current(project.root, project.policy), "reticle": reticle}
    spent, done, stopped = 0.0, [], None
    for n, case in enumerate(cases, 1):
        if spent + need > budget:
            stopped = {"case": case.id, "why": f"${spent:.2f} spent, and the next case needs up to ${need:.2f} "
                                              f"(its ${per_case:.2f} ceiling plus {MARGIN:.0%}) of the ${budget:.2f} budget"}
            say(f"stopped before {case.id}: {stopped['why']}.")
            break
        say(f"[{n}/{len(cases)}] {case.id}: running, ceiling ${per_case:.2f}")
        r = run_case(case, scratch / case.id, per_case, project.policy, pipeline, runner, reticle)
        spent = round(spent + (r.get("cost_usd") or 0), 4)
        _write(out / f"{case.id}.json", r)
        done.append(r)
        say(f"[{n}/{len(cases)}] {case.id}: {r['end']}, hidden tests {r.get('hidden', 'not run')}, "
            f"Second Eye {r.get('second_eye', 'did not run')}"
            + (f", Reticle {r.get('reticle', 'did not run')}" if reticle else "")
            + f", ${r.get('cost_usd') or 0:.2f} (total ${spent:.2f})")
    header.update({"finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   "spent_usd": spent, "done": [r["case"] for r in done], "stopped": stopped})
    _write(out / "run.json", header)
    (out / "summary.md").write_text(summary(project.root, out, header, done), encoding="utf-8")
    return out


# the summary -------------------------------------------------------------------------------------

def unchecked(r: dict) -> bool:
    """The case never reached Second Eye: it crashed, or stopped before the check."""
    return not r.get("verdicts") and r.get("end") != "ready"


def _why(r: dict) -> str:
    why = r.get("error") or (r.get("decisions") or ["it stopped before the check"])[0]
    return why.rstrip(".")


def tally(results: list[dict]) -> dict[str, int]:
    """Second Eye's judgments over every tree it judged, and per case: a fix passed only if it
    reached Ready and passed the hidden tests. A tree nothing reviewed never counts as passed."""
    counts = {"right": 0, "catch": 0, "miss": 0, "false alarm": 0}
    for r in results:
        for v in r.get("verdicts", []):
            counts[v["judgment"]] += 1
    counts["pass"] = sum(r.get("end") == "ready" and r.get("hidden") == "pass" for r in results)
    counts["ready"] = sum(r.get("end") == "ready" for r in results)
    return counts


def summary(root: Path, out: Path, header: dict, results: list[dict]) -> str:
    """The run in the output shape: FYI, a Bottom line, the counts cited to their files, every case
    that never reached the check named under Not looked at, a line per case in Details."""
    rel = out.relative_to(root).as_posix()
    c = tally(results)
    n = len(results)
    stop = header.get("stopped")
    bottom = f"{c['pass']} of {_n(n, 'fix', 'fixes')} reached Ready and passed the hidden tests."
    gaps = []  # cases that never reached the check: (inline, cited)
    for r in results:
        if unchecked(r):
            tree_ = {"pass": "passes", "fail": "fails"}.get(r.get("hidden"), "wasn't tested by")
            gaps.append((f"{r['case']} never reached the check: {_why(r)}",
                         f"{r['case']} never reached the check: {_why(r)}; nothing reviewed its tree, which "
                         f"{tree_} the hidden tests ({rel}/{r['case']}.json:1)"))
    left = [x for x in header["cases"] if x not in header["done"]]
    found = [f"{c['ready']} of {n} reached Ready, for ${header['spent_usd']:.2f} estimated in all ({rel}/run.json:1)",
             f"Second Eye was right {c['right']} times, caught {c['catch']} bad fixes, missed {c['miss']}, "
             f"and raised {c['false alarm']} false alarms ({rel}/run.json:1)"]
    if header.get("reticle"):
        rt = {k: sum(v["judgment"] == k for r in results for v in r.get("reticle_verdicts", []))
              for k in ("right", "catch", "miss", "false alarm")}
        cost = sum(r.get("reticle_cost_usd") or 0 for r in results)
        owed = sum(r.get("reticle_false_alarm_rework_usd") or 0 for r in results)
        found.append(f"Reticle was right {rt['right']} times, caught {rt['catch']} bad fixes, missed {rt['miss']}, and raised "
                     f"{rt['false alarm']} false alarms, for ${cost:.2f} plus ${owed:.2f} of rework its false alarms caused "
                     f"({rel}/run.json:1)")
    if header.get("reticle_inferred"):  # runs of the inferred-outcome experiment, removed after 7eb145
        notes = [n for r in results for n in r.get("inferred_notes", [])]
        found.append(f"inferred-outcome notes the cards would show: {len(notes)}; "
                     f"{sum(bool(r.get('notes_flag_bug')) for r in results)} flagged a real bug, "
                     f"{sum(r.get('notes_noise') or 0 for r in results)} were noise ({rel}/run.json:1)")
    if stop:
        found.append(f"stopped before {stop['case']}: {stop['why']} ({rel}/run.json:1)")
    details = [f"Run {header['run']} on parallax {header['parallax']}, budget ${header['budget_usd']:.2f}, "
               f"ceiling ${header['per_case_usd']:.2f} per case."]
    for r in results:
        ready = f"{r['seconds_to_ready'] / 60:.1f} min to Ready" if r.get("seconds_to_ready") is not None else "never Ready"
        judged = "never judged it" if unchecked(r) else r.get("second_eye", "did not run")
        details.append(f"{r['case']}: ended {r.get('end')}, hidden tests {r.get('hidden', 'not run')}"
                       f"{' on a tree nothing reviewed' if unchecked(r) else ''}, Second Eye {judged}, "
                       + (f"Reticle {r.get('reticle', 'did not run')} (${r.get('reticle_cost_usd') or 0:.2f}), "
                          if header.get("reticle") else "")
                       + f"${r.get('cost_usd') or 0:.2f}, {ready}, {_n(r.get('touches', 0), 'touch', 'touches')}.")
    return _fitted(root, bottom, left, gaps, _next(results, stop), found, details) + "\n"


def _fitted(root: Path, bottom: str, left: list[str], gaps: list[tuple[str, str]], next_: str,
            found: list[str], details: list[str]) -> str:
    """The summary, with every case the budget didn't reach named in the header. Other words are
    shortened to fit, never those names: the unchecked cases move to Found, then Next is cut short."""
    unreached = [f"{', '.join(left)}: the budget didn't reach {'it' if len(left) == 1 else 'them'}"] if left else []
    short_next = next_.replace(", then decide what to change.", ".")  # the names stay; the clause goes
    long_gaps = unreached + ([f"{_n(len(gaps), 'case')} never checked, in Found"] if gaps else [])
    short_gaps = unreached + ([f"{len(gaps)} unchecked, in Found"] if gaps else [])
    cited = [c for _, c in gaps]
    tries = [(unreached + [g for g, _ in gaps], [], next_), (long_gaps, cited, next_), (long_gaps, cited, short_next),
             (short_gaps, cited, short_next), (short_gaps, cited, "you read Details.")]
    text = ""
    for inline, cited, nxt in tries:
        not_looked = "; ".join(inline) or "nothing"
        text = lint.report("FYI", bottom, not_looked, nxt, found + cited, details)
        problems = [m for _, m in lint.lint_report(text, root=root)]
        if any("body is" in m for m in problems):  # the cited gaps go to Details before the header gives way
            text = lint.report("FYI", bottom, not_looked, nxt, found, [c.rsplit(" (", 1)[0] for c in cited] + details)
            problems = [m for _, m in lint.lint_report(text, root=root)]
        if not any("header is" in m for m in problems):
            return text
    return text


def flagged(r: dict) -> bool:
    """A case worth your reading: it crashed, didn't reach Ready, failed the hidden tests, or Second
    Eye or Reticle missed a bad fix or raised a false alarm on any tree."""
    return (r.get("end") != "ready" or r.get("hidden") != "pass"
            or any(v["judgment"] in ("miss", "false alarm") for v in r.get("verdicts", []) + r.get("reticle_verdicts", [])))


def _next(results: list[dict], stop: dict | None) -> str:
    """What's actually true for this run: who does what, if anyone."""
    if not results:
        return "you rerun with a larger budget."
    ids = [r["case"] for r in results if flagged(r)]
    if ids:
        more = f" and {len(ids) - 3} more" if len(ids) > 3 else ""
        return f"you read {', '.join(ids[:3])}{more} in Details, then decide what to change."
    if stop:
        return "you rerun the rest with a larger budget."
    return "nothing needs you: every fix passed and Second Eye was right."


def resummarize(root: Path, out: Path) -> Path:
    """Write a run's summary.md again from its committed JSON files."""
    header = json.loads((out / "run.json").read_text(encoding="utf-8"))
    results = [json.loads((out / f"{c}.json").read_text(encoding="utf-8")) for c in header["done"]]
    (out / "summary.md").write_text(summary(Path(root), out, header, results), encoding="utf-8")
    return out / "summary.md"


def _n(count: int, one: str, many: str = "") -> str:
    return f"{count} {one if count == 1 else (many or one + 's')}"


# staleness, for parallax stats -----------------------------------------------------------------------

def latest(root: Path) -> dict | None:
    """The newest run that finished at least one case."""
    runs = []
    for path in (Path(root) / RESULTS_DIR).glob("*/run.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("done") and data.get("fingerprint"):
            runs.append(data)
    return max(runs, key=lambda d: d["started"]) if runs else None


def staleness(project: Project) -> str | None:
    """One line for stats, or None where there are no evals."""
    if not (project.root / CASES_FILE).is_file():
        return None
    last = latest(project.root)
    if last is None:
        return "no eval has run on the current pipeline. parallax eval --budget runs one."
    moved = fingerprint.changed(last["fingerprint"], fingerprint.current(project.root, project.policy))
    if not moved:
        return f"evals are current: run {last['run']}, {last['started'][:10]}."
    return f"evals are older than {', '.join(moved)}. last run {last['run']}, {last['started'][:10]}."
