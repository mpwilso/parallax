"""The task's venv, made by [build] setup, working wherever the code actually runs.

Setup runs as you, once, on the base commit, never on anything the maker wrote. It runs in a
throwaway git checkout of the base (history and tags included, so a version read from git tags
works), which is deleted afterwards. But the code runs elsewhere: the maker works in the worktree,
the check tests a copy of the reviewed tree, Field runs the app from another copy. An install that
depends on where it ran would then point at the deleted checkout.

So after setup Parallax reads the install's own records, with no per-repo rules:
- import roots: every path into the checkout that a .pth file or an editable finder names
  (src/ for a src layout, the root for a flat one). Where code runs, those roots under that tree go
  on PYTHONPATH, so imports find that tree's code, not the base's.
- generated files: files the install wrote into the checkout that git ignores, inside an import
  root (a _version.py written from git tags, say). They're kept with the venv and laid into each
  tree the code runs from. Git ignores them, so they never show in a diff.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

RECORD = "parallax-install.json"
GENERATED = "parallax-generated"
SKIP_PARTS = {"__pycache__", "build", "dist", ".eggs", ".tox", ".nox", ".venv", "node_modules", ".git"}
MAX_GENERATED = 200


def checkout(repo: Path, commit: str, dest: Path) -> None:
    """A git checkout of commit at dest, sharing repo's objects. Your checkout isn't touched."""
    common = subprocess.run(["git", "-C", str(repo), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                            capture_output=True, text=True, check=True).stdout.strip()
    shutil.rmtree(dest, ignore_errors=True)
    subprocess.run(["git", "clone", "--quiet", "--shared", "--no-checkout", common, str(dest)],
                   capture_output=True, check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "--quiet", "--detach", commit], capture_output=True, check=True)


def make(command: str, repo: Path, commit: str, venv: Path, where: Path) -> tuple[subprocess.CompletedProcess, dict]:
    """Run setup as you in a fresh checkout of commit at where, record what it installed, and
    delete the checkout. Returns (the run, the record)."""
    checkout(repo, commit, where)
    env = {**os.environ, "PARALLAX_VENV": str(venv), "PARALLAX_WORKTREE": str(where)}
    try:
        out = subprocess.run(command, shell=True, cwd=where, env=env, capture_output=True, text=True)
        found = record(venv, where) if out.returncode == 0 and venv.is_dir() else {}
    finally:
        shutil.rmtree(where, ignore_errors=True)
    return out, found


def _site_packages(venv: Path) -> list[Path]:
    return sorted(p for p in venv.glob("lib*/python*/site-packages") if p.is_dir())


def _inside(path: str, where: Path) -> str | None:
    """path relative to where, if it's inside it."""
    try:
        rel = Path(path).resolve().relative_to(where.resolve()) if Path(path).is_absolute() else None
    except ValueError:
        return None
    return None if rel is None else (rel.as_posix() or ".")


def roots(venv: Path, where: Path) -> list[str]:
    """Import roots the install points at inside where, relative to it, in the order found."""
    found: list[str] = []
    prefix = str(where.resolve())
    quoted = re.compile(r"""['"](""" + re.escape(prefix) + r"""(?:/[^'"\n]*)?)['"]""")
    for site in _site_packages(venv):
        for pth in sorted(site.glob("*.pth")):
            for line in pth.read_text(encoding="utf-8", errors="replace").splitlines():
                rel = None if line.startswith(("import ", "import\t", "#")) else _inside(line.strip(), where)
                if rel is not None:
                    found.append(rel)
        for finder in sorted(site.glob("*.py")):  # editable finders map each package to its folder
            for m in quoted.finditer(finder.read_text(encoding="utf-8", errors="replace")):
                rel = _inside(m.group(1), where)
                if rel is not None:
                    found.append(rel if rel == "." else (PurePosixPath(rel).parent.as_posix() or "."))
    return list(dict.fromkeys(found))


def generated(where: Path, import_roots: list[str]) -> list[str]:
    """Files the install wrote inside the import roots that git ignores."""
    out = subprocess.run(["git", "-C", str(where), "ls-files", "-z", "--others", "--ignored", "--exclude-standard"],
                         capture_output=True, text=True).stdout
    keep = []
    for rel in (p for p in out.split("\0") if p):
        parts = PurePosixPath(rel).parts
        if any(x in SKIP_PARTS or x.endswith(".egg-info") for x in parts) or rel.endswith((".pyc", ".pyo")):
            continue
        if any(r == "." or rel.startswith(r.rstrip("/") + "/") for r in import_roots):
            keep.append(rel)
    return keep[:MAX_GENERATED]


def record(venv: Path, where: Path) -> dict:
    """After setup: note the import roots and keep the generated files, with the venv."""
    found = roots(venv, where)
    files = generated(where, found)
    store = venv / GENERATED
    shutil.rmtree(store, ignore_errors=True)
    for rel in files:
        (store / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(where / rel, store / rel)
    data = {"roots": found, "generated": files}
    (venv / RECORD).write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    return data


def _load(venv: Path | None) -> dict:
    try:
        return json.loads((venv / RECORD).read_text(encoding="utf-8")) if venv else {}
    except (OSError, json.JSONDecodeError):
        return {}


def env(venv: Path | None, tree: Path) -> dict[str, str]:
    """PYTHONPATH for code running from tree: the install's import roots under it."""
    found = _load(venv).get("roots") or []
    if not found:
        return {}
    return {"PYTHONPATH": ":".join(str(tree) if r == "." else str(tree / r) for r in found)}


def files(venv: Path | None) -> dict[str, bytes]:
    """The generated files, as rel path -> content."""
    return {rel: (venv / GENERATED / rel).read_bytes() for rel in _load(venv).get("generated") or []
            if (venv / GENERATED / rel).is_file()}


def place(venv: Path | None, tree: Path) -> list[str]:
    """Lay the generated files into tree, where they're missing or differ. Returns what was written."""
    written = []
    for rel, data in files(venv).items():
        target = tree / rel
        if not target.is_file() or target.read_bytes() != data:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written.append(rel)
    return written
