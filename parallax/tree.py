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
import re
import subprocess
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath

from .core import ParallaxError
from .guard import is_protected

EXCLUDE = ":(exclude)docs/tasks"
# build byproducts never count as the change, whatever the repo's .gitignore says (seen live in M12)
BYPRODUCTS = (":(exclude,glob)**/__pycache__/**", ":(exclude,glob)**/*.pyc", ":(exclude,glob)**/.pytest_cache/**")
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
    issues: list[tuple[str, str]] = field(default_factory=list)  # (cause, path) behind each problem


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
    _git(worktree, "add", "-A", "--", ".", *BYPRODUCTS, index=index)
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

    def problem(cause: str, path: str, text: str) -> None:
        s.problems.append(text)
        s.issues.append((cause, path))

    for f in s.files:
        if is_protected(f):
            problem("protected", f, f"{f} is a protected path")
        elif f not in listed:
            problem("outside", f, f"{f} changed but isn't in the plan's files")
    for f in s.binaries:
        if f not in plan["binaries"]:
            problem("binary", f, f"{f} is a binary the plan didn't list")
    for f in s.symlinks:
        if f not in plan["symlinks"]:
            problem("symlink", f, f"{f} is a symlink the plan didn't list")
    if not plan["dependencies"]:
        for f in s.files:
            if _matches(f, DEPENDENCY_FILES):
                problem("dependency", f, f"{f} changed but the plan lists no new dependencies")
    if s.lines > diff_cap:
        s.problems.append(f"the diff is {s.lines} changed lines, over the cap of {diff_cap} for a reliable blind review")
        s.issues.append(("size", ""))
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


def added_lines(diff: str) -> list[tuple[str, int, str]]:
    """(path, line number, text) for every line a unified diff adds."""
    out, path, line = [], "", 0
    for raw in diff.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+"):
            out.append((path, line, raw[1:]))
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return out


# grouping the scope problems for the card: one line per cause, the full list under Details ------------

SECRET_NAME = re.compile(r"(^|/)(\.env(\..*)?|[^/]*\.pem|[^/]*\.key|id_(rsa|ed25519|ecdsa)[^/]*|\.npmrc|\.pypirc|\.netrc"
                         r"|credentials(\.json)?|[^/]*secret[^/]*|[^/]*token[^/]*)$", re.I)
CAUSES = {  # in the order they matter: what's worst comes first
    "protected": "touched a protected path", "outside": "changed outside the plan's files",
    "binary": "are binaries the plan didn't list", "symlink": "are symlinks the plan didn't list",
    "dependency": "changed dependencies the plan doesn't list",
}


def _size(worktree: Path, path: str) -> int | None:
    target = Path(worktree) / path
    return target.stat().st_size if target.is_file() and not target.is_symlink() else None


def _what(n: int | None) -> str:
    return "is deleted or not a file" if n is None else ("is empty" if n == 0 else f"has content ({n} bytes)")


def sensitive(path: str) -> bool:
    return is_protected(path) or bool(SECRET_NAME.search(path))


def describe(s: Staged, worktree: Path) -> tuple[str, list[dict]]:
    """(reason, files): one clause per cause with a count, the real cause first, not the first file.

    A protected or secret-looking file (.env, a key, a token) always says whether it's empty or has
    content, since that changes the decision completely. files: every path, for the card's Details."""
    sizes = {p: _size(worktree, p) for _, p in s.issues if p}
    clauses = [f"{p} looks like a secrets file and {_what(sizes[p])}"
               for p in sorted(sizes) if sensitive(p) and sizes[p]]
    files, seen = [], set()
    for cause, text in CAUSES.items():  # each file counts once, under its worst cause
        paths = [p for c, p in s.issues if c == cause and p not in seen]
        seen.update(paths)
        files += [{"path": p, "cause": cause, "size": sizes[p], "secret": sensitive(p)} for p in paths]
        if len(paths) == 1:
            p = paths[0]
            one = s.problems[s.issues.index((cause, p))]
            clauses.append(one + (f", and it {_what(sizes[p])}" if sensitive(p) and not sizes[p] else ""))
        elif paths:
            known = [sizes[p] for p in paths]
            full = sum(bool(k) for k in known)
            state = ", all empty" if all(k == 0 for k in known) else (f", {full} with content" if full < len(paths) else "")
            names = ", ".join(paths[:2]) + (f" and {len(paths) - 2} more" if len(paths) > 2 else "")
            clauses.append(f"{len(paths)} files {text}{state} ({names})")
    clauses += [t for c, t in zip((c for c, _ in s.issues), s.problems) if c == "size"]
    return "; ".join(clauses), files
