"""Where a task's drafts live: its data folder, never your checkout.

A task's intent, spec, plan, Reticle's tests and Field's flows are written to
~/.local/share/parallax/tasks/<repo>/<task>/drafts/, outside the repo, so an open or dropped task
leaves nothing in your working tree. Everywhere they're named (the ledger, a card's citations, the
record), they keep the path they get in the task's commit, docs/tasks/<task>/..., which accept still
writes exactly as before. resolve() turns that name into the file.

A task begun before this kept its drafts in docs/tasks/<task>/ of the checkout; it carries on reading
and writing them there (no migration), and a merged task's committed copies are read where git put them.
"""
from __future__ import annotations

import re
from pathlib import Path

LOGICAL = re.compile(r"^docs/tasks/([^/]+)/(.+)$")


def folder(root: Path, task_id: str) -> Path:
    from .sandbox import task_home
    return task_home(root, task_id) / "drafts"


def legacy(root: Path, task_id: str) -> Path:
    return Path(root) / "docs" / "tasks" / task_id


def task_dir(root: Path, task_id: str) -> Path:
    """The data folder; for a task that already keeps its drafts in the checkout, that folder."""
    new = folder(root, task_id)
    if new.is_dir():
        return new
    old = legacy(root, task_id)
    return old if old.is_dir() else new


def resolve(root: Path, rel: str) -> Path:
    """A path as the ledger and the cards name it, to the file: docs/tasks/... from the task's folder."""
    m = LOGICAL.match(rel.replace("\\", "/"))
    if m:
        here = task_dir(root, m.group(1)) / m.group(2)
        if here.exists() or not (Path(root) / rel).exists():
            return here
    return Path(root) / rel


def logical(root: Path, path: Path) -> str:
    """A file's name as the ledger and the cards use it: docs/tasks/<task>/... for a draft."""
    path = Path(path)
    try:
        return path.relative_to(Path(root)).as_posix()
    except ValueError:
        pass
    from .sandbox import task_home
    tasks = task_home(root, "x").parent
    try:
        task_id, *rest = path.relative_to(tasks).parts
    except ValueError:
        return str(path)
    if rest[:1] == ["drafts"]:
        return "/".join(["docs", "tasks", task_id, *rest[1:]])
    return str(path)
