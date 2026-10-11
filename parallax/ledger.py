"""Append-only ledger.

Every event is one JSON line. Each line carries the hash of the line before it,
so editing or deleting history breaks the chain and `verify()` catches it.
"""
from __future__ import annotations

import hashlib
import json
import os
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


def _read_only(*_args, **_kw):
    raise TypeError("ledger entries are read-only: they're shared between reads, so change a copy")


def _plain(value):
    """A read-only entry, or any part of one, as ordinary dicts and lists."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


class _Entry(dict):
    """A parsed entry, shared by every read until the file changes, so nothing may change it. It's a
    dict for everything else: json.dumps, dict(e), {**e}; copy.copy and copy.deepcopy give plain ones."""
    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _read_only

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return _plain(self)

    def __reduce_ex__(self, protocol):
        return dict, (_plain(self),)


class _List(list):
    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = extend = insert = pop = remove = clear = sort = \
        reverse = _read_only

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        return _plain(self)

    def __reduce_ex__(self, protocol):
        return list, (_plain(self),)


def _frozen(value):
    if isinstance(value, dict):
        return _Entry((k, _frozen(v)) for k, v in value.items())
    if isinstance(value, list):
        return _List(_frozen(v) for v in value)
    return value


# Parsed entries per ledger file, kept while the file's (size, mtime_ns) stays the same: the signal
# /api/version gives the UI. /api/board reads the ledger several times per task, each request with a
# new Project, so this saves a parse of the whole file every time. A write through Ledger drops its
# file's parse. A few files at most: tests and evals make many short-lived ledgers.
_parsed: dict[Path, tuple[tuple[int, int], list[dict]]] = {}
_parsed_lock = threading.Lock()
_PARSED_FILES = 8


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():  # never touch an existing ledger: its mtime is the UI's version
            self.path.touch()

    def entries(self) -> list[dict]:
        """Every complete entry. Reads take no lock, so a line still being written (no newline yet)
        is left for the next read, never half-parsed (found by the session race test). The entries
        are shared with other reads of the unchanged file, so they're read-only; the list is yours."""
        key = self._stat()
        with _parsed_lock:
            hit = _parsed.get(self.path)
        if hit and hit[0] == key:
            return list(hit[1])
        return self._read(key)

    def _stat(self) -> tuple[int, int]:
        st = os.stat(self.path)
        return st.st_size, st.st_mtime_ns

    def _read(self, key: tuple[int, int] | None = None) -> list[dict]:
        """Parsed from the file, never from the cache. The stat comes first, so a change made while
        reading shows up as a new key on the next read, never as a stale parse kept under it."""
        key = key or self._stat()
        text = self.path.read_text(encoding="utf-8")
        whole = text if text.endswith("\n") else text[:text.rfind("\n") + 1]
        parsed = [_frozen(json.loads(line)) for line in whole.splitlines() if line.strip()]
        with _parsed_lock:
            _parsed.pop(self.path, None)
            _parsed[self.path] = (key, parsed)
            while len(_parsed) > _PARSED_FILES:
                del _parsed[next(iter(_parsed))]
        return list(parsed)

    def _forget(self) -> None:
        with _parsed_lock:
            _parsed.pop(self.path, None)

    def _last_hash(self) -> str:
        entries = self._read()  # under the lock, from the file itself
        return entries[-1]["hash"] if entries else GENESIS

    def append(self, kind: str, actor: str, reason: str = "", **data) -> dict:
        with _exclusive(self.path.with_name(self.path.name + ".lock")):
            return self._write(kind, actor, reason, data)

    def append_if(self, check, kind: str, actor: str, reason: str = "", **data) -> dict | None:
        """Append only if check(entries) is true, deciding and writing under the one lock, so no
        other writer can slip an entry in between."""
        with _exclusive(self.path.with_name(self.path.name + ".lock")):
            if not check(self._read()):
                return None
            return self._write(kind, actor, reason, data)

    def _write(self, kind: str, actor: str, reason: str, data: dict) -> dict:
        """The caller holds the lock."""
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
        try:
            self._drop_torn_tail()
            with self.path.open("a") as f:
                f.write(json.dumps(entry, sort_keys=True) + "\n")
        finally:
            self._forget()
        return entry

    def _drop_torn_tail(self) -> None:
        """The caller holds the lock. A tail with no newline is a line whose writer died mid-write:
        no writer can finish it now, entries() never read it, and the new entry chains past it.
        Appending after it would join the two into one line that no read can parse, so it moves to
        the side file (LEDGER.torn), kept as written, and only then leaves the ledger."""
        raw = self.path.read_bytes()
        if not raw or raw.endswith(b"\n"):
            return
        keep = raw.rfind(b"\n") + 1
        tail = raw[keep:]
        when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with self.path.with_name(self.path.name + ".torn").open("ab") as side:
            side.write(f"torn tail at {when}, {len(tail)} bytes:\n".encode() + tail + b"\n")
            side.flush()
            os.fsync(side.fileno())  # saved for good before the ledger loses it
        with self.path.open("r+b") as f:
            f.truncate(keep)

    def verify(self) -> tuple[bool, str]:
        prev = GENESIS
        for i, entry in enumerate(self._read(), start=1):  # the file itself: a tamper check trusts no cache
            body = {k: v for k, v in entry.items() if k != "hash"}
            if entry.get("prev") != prev:
                return False, f"line {i}: chain broken (prev hash mismatch)"
            if _hash(body) != entry.get("hash"):
                return False, f"line {i}: contents changed after writing"
            prev = entry["hash"]
        return True, "ledger intact"
