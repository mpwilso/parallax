"""Stage the worktree into a temporary index, and check it by code before the checker sees it.

The reviewed tree is `git write-tree` of that index: the whole worktree, new files included,
ignored files left out. Its hash is recorded, and accept (M11) commits exactly that tree.
Checks that need no model, all against the approved plan:
- every changed file is in the plan's file list;
- binaries and symlinks the plan didn't list are refused;
- a changed dependency manifest needs a dependency in the plan;
- protected paths never appear;
- the diff is under the cap (default 400 changed lines);
- files that run automatically (hooks, CI, Makefile, package scripts, .envrc...) are flagged.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .core import ParallaxError
from .guard import is_protected

EXCLUDE = ":(exclude)docs/tasks"
DEPENDENCY_FILES = ("pyproject.toml", "setup.py", "setup.cfg", "requirements*.txt", "Pipfile", "Pipfile.lock",
                    "poetry.lock", "uv.lock", "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                    "go.mod", "go.sum", "Cargo.toml", "Cargo.lock", "Gemfile", "Gemfile.lock")
AUTORUN = (".github/workflows/*", ".gitlab-ci.yml", ".circleci/*", "Makefile", "makefile", "GNUmakefile",
           "Justfile", "justfile", "package.json", ".pre-commit-config.yaml", ".envrc", ".vscode/tasks.json",
           ".vscode/launch.json", ".idea/*", "conftest.py", "*/conftest.py", "setup.py", "tox.ini", "noxfile.py",
           ".husky/*", "lefthook.yml", ".git/hooks/*")


@dataclass
class Staged:
    tree: str
    base: str
    diff: str                        # git diff --cached --binary, without docs/tasks/
    files: list[str]                 # changed paths, without docs/tasks/
    lines: int                       # changed lines, text files only
    binaries: list[str] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)
    autorun: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # blocking: they send the task to you


def _git(wt: Path, *args: str, index: Path | None = None, text: bool = True) -> str:
    env = {**os.environ, "GIT_INDEX_FILE": str(index)} if index else None
    out = subprocess.run(["git", "-C", str(wt), *args], capture_output=True, env=env, text=text,
                         encoding="utf-8" if text else None, errors="replace" if text else None)
    if out.returncode != 0:
        raise ParallaxError(f"git {args[0]} failed: {out.stderr.strip() if text else out.stderr.decode(errors='replace')}")
    return out.stdout


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatch(path, p) or ("/" not in p and fnmatch(name, p)) for p in patterns)


def stage(worktree: Path, base: str, index: Path) -> Staged:
    """The whole worktree, staged into a throwaway index. The worktree's own index isn't touched."""
    index.parent.mkdir(parents=True, exist_ok=True)
    if index.exists():
        index.unlink()
    _git(worktree, "read-tree", base, index=index)
    _git(worktree, "add", "-A", "--", ".", index=index)
    tree = _git(worktree, "write-tree", index=index).strip()
    diff = _git(worktree, "diff", "--cached", "--binary", base, "--", ".", EXCLUDE, index=index)
    files, lines, binaries = [], 0, []
    for row in _git(worktree, "diff", "--cached", "--numstat", "-z", base, "--", ".", EXCLUDE, index=index).split("\0"):
        if not row.strip():
            continue
        added, deleted, path = row.split("\t", 2)
        files.append(path)
        if added == "-":
            binaries.append(path)
        else:
            lines += int(added) + int(deleted)
    base_links = _symlinks(worktree, base)
    symlinks = [p for p in _symlinks(worktree, tree) if p not in base_links]
    return Staged(tree, base, diff, files, lines, binaries, symlinks,
                  autorun=[f for f in files if _matches(f, AUTORUN)])


def _symlinks(worktree: Path, treeish: str) -> set[str]:
    out = _git(worktree, "ls-tree", "-r", "-z", treeish)
    return {row.split("\t", 1)[1] for row in out.split("\0") if row.startswith("120000 ")}


def conform(s: Staged, plan: dict, diff_cap: int) -> Staged:
    """Record every blocking problem the plan makes visible. Code, not a model."""
    listed = set(plan["files"])  # plus verifier tests, once the test-writer exists (eval only for now)
    for f in s.files:
        if is_protected(f):
            s.problems.append(f"{f} is a protected path")
        elif f not in listed:
            s.problems.append(f"{f} changed but isn't in the plan's files")
    for f in s.binaries:
        if f not in plan["binaries"]:
            s.problems.append(f"{f} is a binary the plan didn't list")
    for f in s.symlinks:
        if f not in plan["symlinks"]:
            s.problems.append(f"{f} is a symlink the plan didn't list")
    if not plan["dependencies"]:
        for f in s.files:
            if _matches(f, DEPENDENCY_FILES):
                s.problems.append(f"{f} changed but the plan lists no new dependencies")
    if s.lines > diff_cap:
        s.problems.append(f"the diff is {s.lines} changed lines, over the cap of {diff_cap} for a reliable blind review")
    return s


def export(worktree: Path, tree: str, dest: Path) -> None:
    """Write a tree's files into dest, as plain files. Nothing from the worktree itself."""
    dest.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "-C", str(worktree), "archive", "--format=tar", tree], capture_output=True)
    if archive.returncode != 0:
        raise ParallaxError(f"git archive failed: {archive.stderr.decode(errors='replace').strip()}")
    untar = subprocess.run(["tar", "-x", "-C", str(dest)], input=archive.stdout, capture_output=True)
    if untar.returncode != 0:
        raise ParallaxError(f"tar failed: {untar.stderr.decode(errors='replace').strip()}")


def files_in(worktree: Path, treeish: str) -> list[str]:
    return [p for p in _git(worktree, "ls-tree", "-r", "-z", "--name-only", treeish).split("\0") if p]


def show_file(worktree: Path, treeish: str, path: str) -> bytes:
    out = subprocess.run(["git", "-C", str(worktree), "show", f"{treeish}:{path}"], capture_output=True)
    if out.returncode != 0:
        raise ParallaxError(f"git show {path} failed")
    return out.stdout


def changed_between(worktree: Path, old: str, new: str) -> list[str]:
    return [p for p in _git(worktree, "diff", "--name-only", "-z", old, new, "--", ".", EXCLUDE).split("\0") if p]
