"""Evals: run parallax against real, already-merged fixes and compare with what humans shipped.

Per case: clone the repo as it was before the fix (no later history, so the fix can't be found),
set up a venv, run a real parallax task on the issue text, then score it with the tests the humans
added in their PR. Those tests are hidden from the maker and the checker until scoring.

Evals run under a fixed eval policy with no asks, so nothing waits on a human and no model
approves anything. Results are appended per case, so an interrupted run keeps what it finished.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import tomllib
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .agents.base import AGREE, Agent, Checker
from .core import POLICY_FILE, ParallaxError, Project
from .runner import run_task

CASES_FILE = Path("evals") / "cases.toml"
RESULTS_DIR = Path("evals") / "results"
PER_CASE_BUDGET = 2.50
MAKER_SHARE = 0.8  # of a case's budget; the checker gets the rest


def home() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "parallax" / "evals"


@dataclass
class Case:
    id: str
    repo: str
    base: str
    fix: str
    tests: list[str]
    goal: str
    test_command: str = "python -m pytest -q"
    setup: list[str] = field(default_factory=lambda: ["uv pip install -e . pytest"])
    python: str = ""
    pr: str = ""
    issue: str = ""


def find_cases(start: Path) -> Path:
    for path in [start.resolve(), *start.resolve().parents]:
        if (path / CASES_FILE).exists():
            return path
    raise ParallaxError("no evals/cases.toml here. run this from the parallax repo folder")


def load_cases(root: Path, only: str | None = None) -> list[Case]:
    with open(root / CASES_FILE, "rb") as f:
        cases = [Case(**c) for c in tomllib.load(f).get("case", [])]
    if only:
        cases = [c for c in cases if c.id == only]
        if not cases:
            raise ParallaxError(f"no case {only!r} in evals/cases.toml")
    return cases


# plumbing -----------------------------------------------------------------------------

def _run(args: list[str], cwd: Path, env: dict | None = None, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and res.returncode != 0:
        raise ParallaxError(f"{' '.join(args[:3])} failed: {(res.stderr or res.stdout).strip()[-400:]}")
    return res


def _git(cwd: Path, *args: str) -> str:
    return _run(["git", *args], cwd).stdout.strip()


def _rmtree(path: Path) -> None:
    """Delete a folder, including git's read-only object files on Windows."""
    def writable_then_retry(func, p, exc):
        os.chmod(p, 0o700)
        func(p)
    if path.exists():
        shutil.rmtree(path, onexc=writable_then_retry)


def cache_repo(case: Case) -> Path:
    """A full clone of the upstream repo, fetched once and reused. Agents never see it."""
    name = case.repo.rstrip("/").split("/")[-1].removesuffix(".git") + "-" + uuid.uuid5(uuid.NAMESPACE_URL, case.repo).hex[:6]
    path = home() / "cache" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        _run(["git", "clone", "--quiet", case.repo, str(path)], path.parent)
    elif _run(["git", "cat-file", "-e", case.fix], path, check=False).returncode != 0:
        _git(path, "fetch", "--quiet", "origin")
    return path


def clone_at(cache: Path, commit: str, dest: Path, history_up_to_commit_only: bool = True) -> None:
    """A fresh repo at `commit`. By default it holds no history after that commit."""
    if history_up_to_commit_only:
        branch = f"parallax-eval/{commit[:12]}"
        _git(cache, "branch", "--force", branch, commit)
        _run(["git", "clone", "--quiet", "--no-local", "--single-branch", "--no-tags", "--branch", branch,
              str(cache), str(dest)], dest.parent)
    else:
        _run(["git", "clone", "--quiet", str(cache), str(dest)], dest.parent)
        _git(dest, "checkout", "--quiet", commit)
    _git(dest, "config", "user.email", "evals@parallax.local")
    _git(dest, "config", "user.name", "parallax evals")


def venv_env(venv: Path) -> dict[str, str]:
    scripts = venv / ("Scripts" if sys.platform == "win32" else "bin")
    return {**os.environ, "VIRTUAL_ENV": str(venv), "PATH": str(scripts) + os.pathsep + os.environ.get("PATH", "")}


def setup_env(case: Case, venv: Path, project_dir: Path) -> dict[str, str]:
    """A venv for the case, with the project installed from `project_dir`. Run by the harness."""
    if case.setup:
        if not shutil.which("uv"):
            raise ParallaxError("evals need uv on PATH (https://docs.astral.sh/uv/)")
        _run(["uv", "venv", "--quiet", str(venv), *(["--python", case.python] if case.python else [])], project_dir)
    env = venv_env(venv) if case.setup else dict(os.environ)
    for cmd in case.setup:
        _run(shlex.split(cmd), project_dir, env)
    return env


def run_tests(case: Case, cwd: Path, env: dict[str, str], files: list[str]) -> bool:
    args = shlex.split(case.test_command) + files
    if args[0] == "python" and "VIRTUAL_ENV" in env:  # Windows resolves exes with the parent's PATH
        exe = Path(env["VIRTUAL_ENV"]) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        args[0] = str(exe)
    elif args[0] == "python":
        args[0] = sys.executable
    # no bytecode: a same-size edit within the same second would otherwise run stale .pyc files
    env = {**env, "PYTHONDONTWRITEBYTECODE": "1"}
    return _run(args, cwd, env, check=False).returncode == 0


def put_hidden_tests(case: Case, cache: Path, worktree: Path) -> None:
    """The tests the humans wrote, as of the fix, written over whatever is there."""
    for rel in case.tests:
        data = _run(["git", "show", f"{case.fix}:{rel}"], cache).stdout
        target = worktree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8", newline="\n")


def _diff_lines(repo: Path, a: str, b: str | None = None, exclude: list[str] = ()) -> int:
    args = ["diff", "--numstat", a] + ([b] if b else []) + ["--", "."] + [f":(exclude){p}" for p in exclude]
    total = 0
    for line in _git(repo, *args).splitlines():
        added, removed, _ = line.split("\t", 2)
        total += int(added) + int(removed) if added != "-" else 0
    return total


# check ----------------------------------------------------------------------------------

def check_case(case: Case, workdir: Path) -> str | None:
    """No model, no cost. None if the case is sound; otherwise what's wrong with it."""
    cache = cache_repo(case)
    dest = workdir / case.id
    _rmtree(dest)
    dest.mkdir(parents=True)
    repo = dest / "r"
    clone_at(cache, case.base, repo, history_up_to_commit_only=False)
    env = setup_env(case, dest / "env", repo)
    put_hidden_tests(case, cache, repo)
    if run_tests(case, repo, env, case.tests):
        return "the PR's tests already pass before the fix"
    _git(repo, "checkout", "--quiet", "--force", case.fix)
    if not run_tests(case, repo, env, case.tests):
        return "the PR's tests fail even with the fix"
    return None


# run --------------------------------------------------------------------------------------

EVAL_POLICY = """\
# eval policy: no asks, so nothing waits on a human. only the exact test command may run.
[actions]
"fs.read"  = "allow"
"fs.write" = "allow"

[exact."shell.run"]
{commands}

[limits]
stuck_after = 5
"""


@dataclass
class Result:
    id: str
    outcome: str = "error"          # resolved | unresolved | skipped | error
    task_status: str = ""
    checker: str = ""               # the diff verdict, if the checker ran
    judgment: str = "n/a"           # right | caught | missed | false alarm | n/a
    findings: list[str] = field(default_factory=list)
    inbox: int = 0                  # items that would have reached a human
    cost_usd: float = 0.0
    seconds: float = 0.0
    diff_lines: int = 0
    human_diff_lines: int = 0
    reason: str = ""
    summary: str = ""
    pr: str = ""
    issue: str = ""


def judge(accepted: bool | None, resolved: bool) -> str:
    if accepted is None:
        return "n/a"
    if accepted:
        return "right" if resolved else "missed"
    return "false alarm" if resolved else "caught"


def run_case(case: Case, workdir: Path, maker: Agent, checker: Checker) -> Result:
    started = time.monotonic()
    r = Result(case.id, pr=case.pr, issue=case.issue)
    try:
        cache = cache_repo(case)
        dest = workdir / case.id
        _rmtree(dest)
        dest.mkdir(parents=True)
        repo = dest / "r"
        clone_at(cache, case.base, repo)
        commands = [case.test_command, f'cd "<worktree>" && {case.test_command}']
        (repo / POLICY_FILE).write_text(EVAL_POLICY.format(commands="\n".join(f"{json.dumps(c)} = \"allow\"" for c in commands)))
        proj = Project.init(repo)
        goal = f"{case.goal.strip()}\n\nYou can run the tests with exactly: {case.test_command}"
        t = proj.new_task(goal)
        worktree = Path(t["worktree"])
        env = setup_env(case, dest / "env", worktree)
        maker_env = {k: env[k] for k in ("VIRTUAL_ENV", "PATH") if k in env}

        r.task_status = run_task(proj, t["task"], maker, checker, env=maker_env)
        proj.diff(t["task"])  # marks new files so they count in the diff size
        entries = proj.ledger.entries()
        verdicts = [e for e in entries if e["kind"] == "verdict.recorded" and e["data"]["stage"] == "diff"]
        finished = [e for e in entries if e["kind"] == "maker.finished"]
        r.inbox = len(proj.inbox())
        r.cost_usd = sum(e["data"].get("cost_usd") or 0 for e in entries if e["kind"] in ("maker.finished", "verdict.recorded"))
        r.summary = (finished[-1]["reason"].strip().splitlines() or [""])[0] if finished else ""
        r.diff_lines = _diff_lines(worktree, t["base"], exclude=case.tests)
        r.human_diff_lines = _diff_lines(cache, case.base, case.fix, exclude=case.tests)

        put_hidden_tests(case, cache, worktree)
        resolved = run_tests(case, worktree, env, case.tests)
        r.outcome = "resolved" if resolved else "unresolved"
        accepted = None
        if verdicts:
            v = verdicts[-1]["data"]
            r.checker, r.findings = v["verdict"], v["findings"]
            accepted = v["verdict"] in AGREE
        r.judgment = judge(accepted, resolved)
        if not resolved:
            reasons = {"stuck": "maker got stuck", "maker failed": f"maker stopped: {r.summary}",
                       "disputed": "wrong fix, and the checker flagged it"}
            r.reason = reasons.get(r.task_status, "wrong or incomplete fix")
    except Exception as err:  # a crashed case is a result too, never skipped silently
        r.outcome, r.reason = "error", f"{type(err).__name__}: {err}"[:300]
    r.seconds = round(time.monotonic() - started, 1)
    return r


def run(root: Path, cases: list[Case], make_maker: Callable[[float], Agent], make_checker: Callable[[float], Checker],
        budget: float, say: Callable[[str], None] = print, run_id: str | None = None,
        per_case: float = PER_CASE_BUDGET) -> Path:
    """Run cases in order, stop cleanly at the budget, append each result as it finishes."""
    run_id = run_id or uuid.uuid4().hex[:6]
    out = root / RESULTS_DIR / f"{datetime.now(timezone.utc):%Y-%m-%d}-{run_id}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    workdir = home() / "runs" / run_id
    workdir.mkdir(parents=True, exist_ok=True)
    spent = 0.0
    header = {"run": run_id, "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "budget": budget, "cases": len(cases), "parallax": _git(root, "rev-parse", "--short", "HEAD")}
    with open(out, "a", encoding="utf-8") as f:
        f.write(json.dumps({"header": header}) + "\n")
    for n, case in enumerate(cases, 1):
        cap = min(per_case, budget - spent)
        if cap < per_case / 2:
            r = Result(case.id, outcome="skipped", reason="budget reached", pr=case.pr, issue=case.issue)
        else:
            say(f"[{n}/{len(cases)}] {case.id} ...")
            r = run_case(case, workdir, make_maker(cap * MAKER_SHARE), make_checker(cap * (1 - MAKER_SHARE)))
            spent += r.cost_usd
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps({"result": asdict(r)}) + "\n")
        detail = f"  checker {r.judgment}" if r.judgment != "n/a" else ""
        say(f"[{n}/{len(cases)}] {case.id}: {r.outcome}{detail}  ${r.cost_usd:.2f}  (total ${spent:.2f} of ${budget:.2f})"
            + (f"  {r.reason}" if r.reason and r.outcome != "resolved" else ""))
    write_report(out)
    return out


# report -------------------------------------------------------------------------------------

def read_run(path: Path) -> tuple[dict, list[Result]]:
    header, results = {}, []
    for line in path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if "header" in rec:
            header = rec["header"]
        else:
            results.append(Result(**rec["result"]))
    return header, results


def _cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def write_report(jsonl: Path) -> Path:
    header, results = read_run(jsonl)
    ran = [r for r in results if r.outcome != "skipped"]
    count = lambda pred: sum(1 for r in results if pred(r))
    cost = sum(r.cost_usd for r in results)
    minutes = sum(r.seconds for r in results) / 60
    lines = [
        f"# Parallax eval run {header.get('run', '?')}",
        "",
        f"Started {header.get('started', '?')} on parallax {header.get('parallax', '?')}. "
        f"{len(results)} cases, budget ${header.get('budget', 0):.2f}.",
        "",
        "Each case is a real merged fix. Parallax got the issue text only. The tests the humans added "
        "in their PR were hidden from the maker and the checker, and used afterwards to score the result.",
        "",
        "## Summary",
        "",
        "| | |",
        "|---|---|",
        f"| resolved (the humans' tests pass) | {count(lambda r: r.outcome == 'resolved')} of {len(ran)} run |",
        f"| checker right (accepted a good fix) | {count(lambda r: r.judgment == 'right')} |",
        f"| checker caught a bad fix | {count(lambda r: r.judgment == 'caught')} |",
        f"| checker missed a bad fix (would have reached you as ready) | {count(lambda r: r.judgment == 'missed')} |",
        f"| checker false alarm (flagged a good fix) | {count(lambda r: r.judgment == 'false alarm')} |",
        f"| items that would have reached your inbox | {sum(r.inbox for r in results)} |",
        f"| errors / skipped | {count(lambda r: r.outcome == 'error')} / {count(lambda r: r.outcome == 'skipped')} |",
        f"| cost | ${cost:.2f} |",
        f"| time | {minutes:.0f} min |",
        "",
        "## Cases",
        "",
        "| case | result | checker | cost | time | lines changed (parallax / human) |",
        "|---|---|---|---|---|---|",
    ]
    for r in results:
        checker = f"{r.checker}, {r.judgment}" if r.checker else "did not run"
        lines.append(f"| [{r.id}]({r.pr or '#'}) | {r.outcome} | {checker} | ${r.cost_usd:.2f} | "
                     f"{r.seconds / 60:.1f} min | {r.diff_lines} / {r.human_diff_lines} |")
    lines += ["", "## Details", ""]
    for r in results:
        lines += [f"### {r.id}: {r.outcome}", ""]
        links = ", ".join(f"[{k}]({v})" for k, v in (("issue", r.issue), ("pull request", r.pr)) if v)
        if links:
            lines += [links, ""]
        if r.reason:
            lines += [f"Why: {_cell(r.reason)}", ""]
        if r.summary:
            lines += [f"Maker said: {_cell(r.summary)[:300]}", ""]
        if r.checker:
            lines += [f"Checker: {r.checker} ({r.judgment})."]
            lines += [f"- {_cell(f)[:300]}" for f in r.findings]
            lines += [""]
    report = jsonl.with_suffix(".md")
    report.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return report


def summary_line(jsonl: Path) -> str:
    header, results = read_run(jsonl)
    ran = [r for r in results if r.outcome != "skipped"]
    resolved = sum(r.outcome == "resolved" for r in results)
    missed = sum(r.judgment == "missed" for r in results)
    caught = sum(r.judgment == "caught" for r in results)
    return (f"{resolved} of {len(ran)} real fixes resolved; the checker caught {caught} bad fixes and missed {missed}; "
            f"${sum(r.cost_usd for r in results):.2f} total")
