"""Parallax cleans up after itself: a merged or dropped task leaves no worktree or branch behind.

- Merged: once your next command sees the accepted commit in its branch unchanged, the task's worktree
  is removed and its branch deleted. The card no longer needs either: it reads the ledger and the
  commits, which are in your repo.
- Dropped: the work so far, everything in the worktree that differs from the base, is kept as a patch
  in the task's data folder first (its path and hash go in the ledger), then the worktree and branch
  go. A task begun before drafts moved out of your checkout has them moved to its data folder too.
Only Parallax's own worktrees are ever removed, by their exact path, never yours. A branch checked
out somewhere else is left, and the ledger says so. Tasks merged or dropped before this are left alone.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

from . import drafts, sandbox, tree
from .core import Project, worktrees_home


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)


def _remove(project: Project, task_id: str) -> dict:
    """Remove the task's worktree and delete its branch. Returns what happened, for the ledger."""
    t = project.task(task_id)
    wt, done = Path(t["worktree"]), {"worktree_removed": False, "branch_deleted": False}
    own = worktrees_home(project.root).resolve()
    if wt.resolve().is_relative_to(own) and wt.resolve() != own:  # Parallax's own, by its exact path
        if wt.is_dir():
            _git(project.root, "worktree", "remove", "--force", str(wt))
        _git(project.root, "worktree", "prune")
        done["worktree_removed"] = not wt.exists()
    branch = t.get("branch") or ""
    if branch and _git(project.root, "rev-parse", "-q", "--verify", f"refs/heads/{branch}").returncode == 0:
        out = _git(project.root, "branch", "-D", branch)
        done["branch_deleted"] = out.returncode == 0
        if out.returncode:
            done["branch_kept"] = (out.stderr.strip().splitlines() or ["git refused"])[-1].removeprefix("error: ")
    return done


def merged(project: Project, task_id: str, target: str | None) -> dict | None:
    """After merge.confirmed: the branch is in target, so nothing on it is lost."""
    branch = project.task(task_id).get("branch") or ""
    ref = f"refs/heads/{target}" if target else "HEAD"
    tip = _git(project.root, "rev-parse", "-q", "--verify", f"refs/heads/{branch}")
    if tip.returncode == 0 and _git(project.root, "merge-base", "--is-ancestor", tip.stdout.strip(), ref).returncode != 0:
        return project.ledger.append("task.cleaned", "parallax", f"kept {branch}: it has commits that aren't in {target}",
                                     task=task_id, worktree_removed=False, branch_deleted=False)
    return project.ledger.append("task.cleaned", "parallax", "merged: its worktree and branch aren't needed",
                                 task=task_id, **_remove(project, task_id))


def _patch(project: Project, task_id: str) -> dict:
    """The worktree's work against the base, as a patch in the task's data folder: {patch, patch_sha}."""
    t = project.task(task_id)
    wt = Path(t["worktree"])
    if not wt.is_dir():
        return {}
    home = sandbox.task_home(project.root, task_id)
    home.mkdir(parents=True, exist_ok=True)
    staged = tree.stage(wt, t["base"], home / "drop.index")
    (home / "drop.index").unlink(missing_ok=True)
    if not staged.diff.strip():
        return {}
    data = staged.diff.encode("utf-8", errors="surrogateescape")
    path = home / "dropped.patch"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return {"patch": str(path), "patch_sha": hashlib.sha256(data).hexdigest(), "files": staged.files}


def _drafts_out_of_checkout(project: Project, task_id: str) -> str | None:
    """An older task's untracked drafts in docs/tasks/<task>/ of your checkout move to its data folder."""
    old = drafts.legacy(project.root, task_id)
    tracked = _git(project.root, "ls-files", "--", f"docs/tasks/{task_id}").stdout.strip()
    if not old.is_dir() or tracked:
        return None
    new = drafts.folder(project.root, task_id)
    if new.exists():
        return None
    new.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(new))
    if not any(drafts.legacy(project.root, "x").parent.iterdir()):
        drafts.legacy(project.root, "x").parent.rmdir()  # docs/tasks/ itself, once empty
    return str(new)


def dropped(project: Project, task_id: str, reason: str) -> dict:
    """Your drop: recorded, then the work kept as a patch, then the worktree and branch removed.
    A task still running keeps both until it stops: nothing is removed under a live process."""
    from .build import running_builds
    e = project.ledger.append("task.rejected", "human", reason, task=task_id, was=project.task(task_id)["status"])
    if task_id in running_builds(project):
        project.ledger.append("task.cleaned", "parallax", "kept its worktree: it was still running",
                              task=task_id, worktree_removed=False, branch_deleted=False)
        return e
    kept = _patch(project, task_id)
    moved = _drafts_out_of_checkout(project, task_id)
    project.ledger.append("task.cleaned", "parallax",
                          "dropped: its work is kept as a patch" if kept else "dropped: there was no work to keep",
                          task=task_id, **kept, **({"drafts_moved_to": moved} if moved else {}), **_remove(project, task_id))
    return e
