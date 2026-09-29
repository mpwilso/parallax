"""`parallax build`: the maker builds an approved plan inside the sandbox. `parallax stop` ends it.

Before launch, in your terminal:
- the plan's approval must verify, and every approved file must still match its hash;
- the budget is what's left of the plan's cap, counting every cost the task has recorded,
  drafting included (costs.py). With nothing left, it won't launch;
- the policy's [build] setup makes the task's venv once, as you, before any maker exists;
- the sandbox rules are generated from the plan and written outside the worktree;
- preflight must pass.
Then the builder runs in its own process group, started with a scrubbed environment: no keys or
tokens, and CLAUDE_CODE_SUBPROCESS_ENV_SCRUB set. The build never pauses; the maker never commits.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import costs, guard, lifecycle, lint, preflight, sandbox
from .agents.base import Agent
from .core import ROOT_ENV, TASK_ENV, ParallaxError, Project, inside_task, refuse_inside_task
from .gate import Scope, make_permission_fn
from .runner import _make

KEEP_ENV = ("HOME", "USER", "LOGNAME", "LANG", "LANGUAGE", "TERM", "SHELL", "TZ",
            "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_RUNTIME_DIR")
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
QUIET_BUILD = {"PYTHONDONTWRITEBYTECODE": "1", "PYTEST_ADDOPTS": "-p no:cacheprovider"}  # no byproducts in the worktree


@dataclass
class Prepared:
    task: dict
    plan: dict
    worktree: Path
    home: Path
    venv: Path | None
    scope: Scope
    rules: sandbox.Rules
    settings: Path
    cap: float
    left: float


def scrubbed_env(venv: Path | None, environ: dict | None = None, path: str | None = None) -> dict[str, str]:
    """Only what a build needs: identity, locale, where Parallax keeps things, and a plain PATH.

    path: keep this PATH instead (the pilot, whose setup command needs your tools)."""
    environ = dict(os.environ if environ is None else environ)
    env = {k: v for k, v in environ.items() if k in KEEP_ENV or k.startswith("LC_")}
    env["PATH"] = path or ((f"{venv}/bin:" if venv else "") + SYSTEM_PATH)
    if venv:
        env["VIRTUAL_ENV"] = str(venv)
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    env.update(QUIET_BUILD)
    return env


def goal(project: Project, task_id: str) -> str:
    parts = ["Build this task by following its approved plan. Change only the files the plan lists."]
    for doc in ("intent", "spec", "plan"):
        path = lifecycle.doc_path(project, task_id, doc)
        if path.exists():
            parts.append(f"The approved {doc}:\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


def _setup_venv(project: Project, task_id: str, worktree: Path, home: Path) -> Path | None:
    """Run [build] setup once, as you. Only on a clean worktree still at its base commit: setup may
    run repo code (a build backend, say), and nothing the maker wrote may ever run as you."""
    command = project.policy.build["setup"].strip()
    if not command:
        return None
    venv = home / "venv"
    if venv.exists():
        return venv
    head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(worktree), "status", "--porcelain"], capture_output=True, text=True).stdout
    if head != project.task(task_id)["base"] or dirty.strip():
        raise ParallaxError("the task's venv is missing and its worktree has changes, so setup won't run: "
                            "it could run code the maker wrote as you")
    env = {**os.environ, "PARALLAX_VENV": str(venv), "PARALLAX_WORKTREE": str(worktree)}
    out = subprocess.run(command, shell=True, cwd=worktree, env=env, capture_output=True, text=True)
    project.ledger.append("setup.ran", "parallax", command, task=task_id, exit=out.returncode)
    if out.returncode != 0:
        tail = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["no output"]
        raise ParallaxError(f"the [build] setup command failed: {tail[0]}")
    return venv


def prepare(project: Project, task_id: str, setup: bool = True, launching: bool = True) -> Prepared:
    """Everything checked and generated before a launch, without launching.

    launching=False is the builder itself, which runs after its launch was recorded."""
    refuse_inside_task(project.root)
    t = lifecycle.lifecycle_task(project, task_id)
    st = lifecycle.state(project, task_id)
    if st.gate is not None:
        raise ParallaxError(f"the plan for {task_id} isn't approved yet. run parallax approve {task_id}")
    for e in st.approved:
        for doc, sha in e["data"]["files"].items():
            path = lifecycle.doc_path(project, task_id, doc)
            if not path.exists() or lifecycle.file_hash(path) != sha:
                raise ParallaxError(f"{lifecycle.rel(project, path)} changed after you approved it. put it back as it was")
    if launching and task_id in running_builds(project):
        raise ParallaxError(f"task {task_id} is already building. parallax stop ends it")
    plan = lifecycle.plan_data(project, task_id)
    if plan is None:
        raise ParallaxError(f"the approved plan for {task_id} has no readable toml block")
    cap, left = costs.budget(project, task_id, plan)
    if launching and left <= 0:
        raise ParallaxError(f"task {task_id} has used its budget cap (${cap:.2f} estimated)")

    wt = Path(t["worktree"])
    home = sandbox.task_home(project.root, task_id)
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    venv = _setup_venv(project, task_id, wt, home) if setup else (home / "venv" if (home / "venv").exists() else None)
    reads = [str(Path(r).expanduser()) for r in plan["outside_reads"]]
    scope = Scope(reads=tuple(Path(p) for p in reads) + ((venv,) if venv else ()), domains=tuple(plan["domains"]))
    targets = sandbox.protected_targets(wt)
    sandbox.prepare_mount_points(targets)
    r = sandbox.rules(wt, targets, git_dir=sandbox.shared_git_dir(wt), venv=venv,
                      reads=reads, domains=plan["domains"])
    settings, _ = sandbox.write_configs(home, r)
    return Prepared(t, plan, wt, home, venv, scope, r, settings, cap, left)


def run_preflight(project: Project, p: Prepared, runner=None) -> list[preflight.Line]:
    return preflight.run(project, p.task["task"], p.worktree, p.rules, p.home, p.scope,
                         scrubbed_env(p.venv), runner or preflight.run_srt)


def _spawn(argv: list[str], env: dict, cwd: Path, log: Path) -> int:
    with open(log, "ab") as out:
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, start_new_session=True).pid


def launch(project: Project, p: Prepared, spawn: Callable | None = None, mode: str = "build") -> int:
    """Start the builder in the background with a scrubbed environment. Returns its pid.

    mode: "build" builds, then checks; "check" only checks what's already built."""
    argv = [sys.executable, "-u", "-m", "parallax.build", str(project.root), p.task["task"], mode]
    pid = (spawn or _spawn)(argv, scrubbed_env(p.venv), project.root, p.home / "build.log")
    project.ledger.append("build.started", "parallax", "", task=p.task["task"], pid=pid, budget_usd=p.left,
                          settings=str(p.settings), mode=mode)
    return pid


def run_build(project: Project, task_id: str, maker_for: Callable[[float, str], Agent], extra: str = "") -> str:
    """Run the maker once, then record what came of it. extra: what a rework must fix."""
    if inside_task(project.root):
        raise ParallaxError("tasks can't start builds")
    p = prepare(project, task_id, setup=False, launching=False)
    if p.left <= 0:
        return costs.stop_at_cap(project, task_id, p.cap)
    env = {TASK_ENV: task_id, ROOT_ENV: str(project.root), **QUIET_BUILD,
           "PATH": (f"{p.venv}/bin:" if p.venv else "") + SYSTEM_PATH}  # set here, whatever the pilot's PATH
    if p.venv:
        env["VIRTUAL_ENV"] = str(p.venv)
    fn = make_permission_fn(project, task_id, p.worktree, scope=p.scope)
    before = sandbox.untracked(p.worktree)
    res = _make(project, task_id, maker_for(p.left, str(p.settings)), goal(project, task_id) + (f"\n\n{extra}" if extra else ""), "build",
                 fn, extra_env=env)

    removed = sandbox.remove_leftovers(p.worktree, before)
    if removed:
        project.ledger.append("sandbox.cleaned", "parallax", "removed the sandbox's empty placeholder files",
                              task=task_id, files=removed)
    touched = guard.protected_in(project.diff(task_id, "--name-only").splitlines())
    if touched:  # the backstop behind both layers: nested protected files made after launch, say
        why = f"diff touches protected files: {', '.join(touched)}"
        project.ledger.append("guard.tripped", "parallax", why, task=task_id, action="fs.write", why=why)
        project.ledger.append("disagreement.raised", "parallax", why, task=task_id, stage="guard")
        status = "disputed"
    elif project.task(task_id)["status"] == "stuck":
        status = "stuck"
    elif costs.budget(project, task_id, p.plan)[1] <= 0 or "budget cap" in (res.summary or ""):
        status = "over budget"  # recorded below, and it comes to you
    elif res.status == "conflict":
        status = "disputed"
        project.ledger.append("disagreement.raised", "parallax",
                              f"the maker says a finding goes against your approved plan: {res.summary.strip()[:300]}",
                              task=task_id, stage="conflict")
    elif res.status == "done":
        status = "built"
    else:
        status = "blocked" if res.summary.strip().lower().startswith("blocked:") else "maker failed"
    first = res.summary.strip().splitlines()[0][:200] if res.summary.strip() else ""
    project.ledger.append("build.finished", "parallax", first, task=task_id, status=status)
    if status == "over budget":
        return costs.stop_at_cap(project, task_id, p.cap)
    if status == "blocked":  # a refusal made the task impossible: it comes to you now, not after rework
        project.ledger.append("stuck.raised", "parallax", first, task=task_id)
    return status


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_builds(project: Project) -> dict[str, int]:
    """task -> builder pid, for tasks whose build hasn't finished or been stopped."""
    out: dict[str, int] = {}
    legacy: set[str] = set()  # started before builds had a mode (M9): those end at build.finished
    for e in project.ledger.entries():
        tid = e["data"].get("task")
        if e["kind"] == "build.started":
            out[tid] = e["data"]["pid"]
            legacy.discard(tid) if "mode" in e["data"] else legacy.add(tid)
        elif e["kind"] in ("builder.finished", "task.stopped") or (e["kind"] == "build.finished" and tid in legacy):
            out.pop(tid, None)
    return out


def stop(project: Project, grace: float = 3.0) -> list[str]:
    """End every running build now, and record it. No questions."""
    refuse_inside_task(project.root)
    stopped = []
    for tid, pid in running_builds(project).items():
        if _alive(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.monotonic() + grace
            while _alive(pid) and time.monotonic() < deadline:
                time.sleep(0.1)
            if _alive(pid):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
        project.ledger.append("task.stopped", "human", "parallax stop", task=tid, pid=pid)
        stopped.append(tid)
    return stopped


def _maker(left: float, settings: str) -> Agent:
    from .agents.claude import ClaudeAgent
    return ClaudeAgent(max_budget_usd=left, settings=settings)


def _drafter(cap: float) -> Agent:
    from .agents.claude import ClaudeAgent
    return ClaudeAgent(max_budget_usd=cap)


def _checker(left: float, model: str):
    from .agents.claude import ClaudeChecker
    return ClaudeChecker(model=model, max_budget_usd=left)


def run_mode(project: Project, task_id: str, mode: str, drafter_for, maker_for, checker_for, *,
             test_runner=None, preflight_runner=None) -> str:
    """What a background process runs, start to finish. The last entry marks it finished, whatever happened.

    mode: "pilot" (draft, launch rule, build, check), "build" (build, then check), or "check"."""
    from .check import run_check
    from .pilot import run

    status = "error"
    try:
        if mode == "pilot":
            status = run(project, task_id, drafter_for, maker_for, checker_for,
                         test_runner=test_runner, preflight_runner=preflight_runner)
        else:
            status = run_build(project, task_id, maker_for) if mode == "build" else "built"
            if status == "built":
                status = run_check(project, task_id, checker_for, maker_for,
                                   test_runner=test_runner, preflight_runner=preflight_runner)
    except Exception as err:  # nothing fails quietly in the background: it comes to you
        project.ledger.append("stuck.raised", "parallax", lint.one_sentence(
            f"Parallax hit an error and stopped the task: {type(err).__name__}: {err}"), task=task_id, error=True)
        status = "stuck"
    finally:
        project.ledger.append("builder.finished", "parallax", "", task=task_id, status=status)
    return status


def main(argv: list[str]) -> int:
    root, task_id, mode = argv
    status = run_mode(Project(Path(root)), task_id, mode, _drafter, _maker, _checker)
    print(f"{mode} {task_id}: {status}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
