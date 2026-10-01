"""The full output of a failing check, kept as a file, with its hash in the ledger.

The ledger keeps only the last lines of a test run, so a long failure was lost. Now, when the
plan's tests, Reticle's tests or Field's flows fail, the whole output goes to a file in the task's
data folder (outside the repo and the worktree), and the ledger entry records its path and sha256.
The file is read back only through `read`, which refuses one whose hash no longer matches: the
ledger stays the witness for what the check printed.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

from . import sandbox
from .core import Project

FOLDER = "check-output"


def keep(project: Project, task_id: str, name: str, text: str) -> dict:
    """Write text to the task's data folder. Returns the ledger fields: {output, output_sha}."""
    data = text.encode("utf-8", errors="replace")
    sha = hashlib.sha256(data).hexdigest()
    folder = sandbox.task_home(project.root, task_id) / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}-{sha[:12]}.txt"
    if not path.exists():
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
    return {"output": str(path), "output_sha": sha}


class Tampered(ValueError):
    pass


def read(entry: dict) -> str:
    """The full output an entry recorded, after checking its hash."""
    d = entry.get("data") or {}
    if not d.get("output"):
        raise ValueError(f"ledger entry {entry.get('id')} kept no full output")
    path = Path(d["output"])
    try:
        data = path.read_bytes()
    except OSError:
        raise ValueError(f"the full output of ledger entry {entry.get('id')} is gone from {path}") from None
    if hashlib.sha256(data).hexdigest() != d.get("output_sha"):
        raise Tampered(f"the full output of ledger entry {entry.get('id')} changed after it was recorded: its hash doesn't match")
    return data.decode("utf-8", errors="replace")


def latest(project: Project, task_id: str) -> dict | None:
    """The task's last entry that kept a full output, this attempt."""
    from .status import attempt
    kept = [e for e in attempt(project.ledger.entries(), task_id) if e["data"].get("output_sha")]
    return kept[-1] if kept else None
