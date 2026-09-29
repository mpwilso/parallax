"""Append-only ledger.

Every event is one JSON line. Each line carries the hash of the line before it,
so editing or deleting history breaks the chain and `verify()` catches it.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64
_append_lock = threading.Lock()  # parallel tool calls append from several threads

if sys.platform == "win32":
    import msvcrt

    def _lock(f) -> None:
        f.seek(0)
        while True:
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # gives up after ~10s, so loop
                return
            except OSError:
                continue

    def _unlock(f) -> None:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_EX)

    def _unlock(f) -> None:
        fcntl.flock(f, fcntl.LOCK_UN)


@contextmanager
def file_lock(lock_path: Path):
    """Exclusive across processes, blocking. The OS drops the lock if the holder dies."""
    with open(lock_path, "a+b") as f:
        _lock(f)
        try:
            yield
        finally:
            _unlock(f)


@contextmanager
def _exclusive(lock_path: Path):
    """One appender at a time, across threads and processes."""
    with _append_lock, file_lock(lock_path):
        yield


def _hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():  # never touch an existing ledger: its mtime is the UI's version
            self.path.touch()

    def entries(self) -> list[dict]:
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def _last_hash(self) -> str:
        entries = self.entries()
        return entries[-1]["hash"] if entries else GENESIS

    def append(self, kind: str, actor: str, reason: str = "", **data) -> dict:
        with _exclusive(self.path.with_name(self.path.name + ".lock")):
            body = {
                "id": uuid.uuid4().hex[:8],
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "kind": kind,
                "actor": actor,
                "reason": reason,
                "data": data,
                "prev": self._last_hash(),
            }
            entry = {**body, "hash": _hash(body)}
            with self.path.open("a") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        return entry

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, entry in enumerate(self.entries(), start=1):
            body = {k: v for k, v in entry.items() if k != "hash"}
            if entry.get("prev") != prev:
                return False, f"line {i}: chain broken (prev hash mismatch)"
            if _hash(body) != entry.get("hash"):
                return False, f"line {i}: contents changed after writing"
            prev = entry["hash"]
        return True, "ledger intact"
