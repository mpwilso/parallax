"""Checks a hash-chained ledger in either of two formats, and only reads it.

- v0 is Parallax's own ledger (parallax/ledger.py): one `json.dumps(entry, sort_keys=True)` per line,
  each entry's `hash` over the rest of it, each `prev` the hash before.
- v1 is the portable chain of Polarizer, the same author's MCP proxy, specified in its
  docs/LEDGER-SPEC.md: canonical JSON lines with a `seq`, a hash prefixed `LEDGER-SPEC/1`, a genesis
  entry naming the chain, and an optional `ledger.head` file beside it.

Written here from that spec (docs/prior-art.md credits it), not from Polarizer's code. A ledger is all
v0 or all v1, decided by its first line. The verdict is one status with the line, the seq and the
check that decided it, in the spec's order: problems in a line, in file order; then the head file;
then a torn tail. conformance/ holds Polarizer's fixtures and their expected verdicts, and
tests/test_conformance.py runs this over every one. Parallax's own `Ledger.verify()` is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ZEROS = "0" * 64
V1_HASH_PREFIX = b"LEDGER-SPEC/1\n"
V0_KEYS = ("id", "ts", "kind", "actor", "reason", "data", "prev", "hash")
V1_KEYS = ("v", "seq", "ts", "kind", "data", "prev", "hash")  # the spec's table order: a missing key is named in it
V1_TYPES = {"v": int, "seq": int, "ts": str, "kind": str, "data": dict, "prev": str, "hash": str}
HEAD_KEYS = {"chain_id", "hash", "seq"}
MAX_LINE = 16 * 1024  # bytes, the newline included (v1 only)
MAX_INT = 2 ** 53 - 1
HEX32 = re.compile(r"[0-9a-f]{32}")
HEX64 = re.compile(r"[0-9a-f]{64}")
GENESIS = "ledger.genesis"


@dataclass(frozen=True)
class Verdict:
    """status: intact, tampered, invalid, not canonical, torn tail, truncated, or empty (no bytes at all).
    check: the check that decided it (structure, seq, prev, hash, canonical, head), None when intact or
    torn. line and seq are None where they don't apply: a head problem has no line, and v0 has no seq.
    A torn tail's line and seq are the last complete line's (line 0 and no seq when there is none)."""
    status: str
    check: str | None
    line: int | None
    seq: int | None
    reason: str
    version: int | None = None
    entries: int = 0
    head: str | None = None  # the last complete entry's hash
    chain_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "intact"

    def text(self) -> str:
        """One line, for the terminal."""
        if self.status == "intact":
            if self.version == 0:
                return f"intact (v0): {self.entries} entries, head {self.head} at line {self.entries}"
            return f"intact (v1): {self.entries} entries, chain {self.chain_id}, head {self.head} at seq {self.seq}"
        if self.status == "empty":
            return "empty: the ledger has no entries"
        if self.status == "torn tail":
            seq = "" if self.version == 0 else f" (seq {'?' if self.seq is None else self.seq})"
            return f"torn tail: {self.reason}{seq}"
        if self.line is None:
            return f"{self.status}: {self.reason}"
        where = f"line {self.line}" + ("" if self.version == 0 else f" (seq {'?' if self.seq is None else self.seq})")
        return f"{self.status}: {where}: {self.reason}"


def canonical(value) -> bytes:
    """v1's canonical bytes. Inside the spec's subset (ASCII keys, integers within 2^53, no floats, no
    lone surrogates) this equals RFC 8785, which is what the spec relies on."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def v1_hash(entry: dict) -> str:
    return hashlib.sha256(V1_HASH_PREFIX + canonical({k: v for k, v in entry.items() if k != "hash"})).hexdigest()


def v0_hash(entry: dict) -> str:
    """Frozen: exactly what parallax/ledger.py has always hashed."""
    return hashlib.sha256(json.dumps({k: v for k, v in entry.items() if k != "hash"}, sort_keys=True).encode()).hexdigest()


def _int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _pointer(path: list) -> str:
    """A JSON pointer, printed in ASCII."""
    def part(p) -> str:
        s = str(p).replace("~", "~0").replace("/", "~1")
        return "".join(c if " " <= c <= "~" else f"\\u{ord(c):04x}" for c in s)
    return "/" + "/".join(part(p) for p in path)


def _outside_subset(value, path: list) -> str | None:
    """The first value outside v1's subset, in document order, as the rule it breaks."""
    if isinstance(value, dict):
        for k, v in value.items():
            if not k.isascii():
                return f"non-ASCII key at {_pointer(path + [k])}"
            found = _outside_subset(v, path + [k])
            if found:
                return found
    elif isinstance(value, list):
        for i, v in enumerate(value):
            found = _outside_subset(v, path + [i])
            if found:
                return found
    elif isinstance(value, float):
        return f"float at {_pointer(path)}"
    elif _int(value) and not -MAX_INT <= value <= MAX_INT:
        return f"integer out of range at {_pointer(path)}"
    elif isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        return f"lone surrogate at {_pointer(path)}"
    return None


def _parse(raw: bytes):
    """(the parsed line, None) or (None, the structure rule it breaks)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, "not valid UTF-8"
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):  # ValueError covers JSONDecodeError and huge integer literals
        return None, "not valid JSON"
    if not isinstance(value, dict):
        return None, "not a JSON object"
    return value, None


class _Chain:
    """What the lines so far established."""

    def __init__(self):
        self.version: int | None = None
        self.hashes: list[str] = []  # each complete entry's stored hash, by line
        self.chain_id: str | None = None


def _line(chain: _Chain, n: int, raw: bytes) -> Verdict | None:
    """Check one complete line (without its newline). None: it passes, and the chain has grown."""
    entry, broken = _parse(raw)
    if n == 1 and entry is not None:
        chain.version = 1 if "v" in entry else 0
    v1 = chain.version != 0
    seq = entry.get("seq") if v1 and entry is not None and _int(entry.get("seq")) else None

    def bad(status: str, check: str, reason: str) -> Verdict:
        return Verdict(status, check, n, seq, reason, chain.version, len(chain.hashes), chain_id=chain.chain_id)

    if broken:
        return bad("invalid", "structure", broken)
    if ("v" in entry) != (chain.version == 1):
        return bad("invalid", "structure", "v0 and v1 entries mixed")
    keys = V1_KEYS if v1 else V0_KEYS
    if v1 and len(raw) + 1 > MAX_LINE:
        return bad("invalid", "structure", "line longer than 16 KiB")
    extra = next((k for k in entry if k not in keys), None)
    if extra is not None:
        return bad("invalid", "structure", f'unknown key "{_pointer([extra])[1:]}"')
    missing = next((k for k in keys if k not in entry), None)
    if missing is not None:
        return bad("invalid", "structure", f'missing key "{missing}"')
    if v1:
        wrong = next((k for k in V1_KEYS if not (_int(entry[k]) if V1_TYPES[k] is int else isinstance(entry[k], V1_TYPES[k]))), None)
        if wrong is not None:
            return bad("invalid", "structure", f'"{wrong}" has the wrong type')
        if entry["v"] != 1:
            return bad("invalid", "structure", "v is not 1")
        outside = _outside_subset(entry, [])
        if outside:
            return bad("invalid", "structure", outside)
        if n == 1 and entry["kind"] != GENESIS:
            return bad("invalid", "structure", "first entry is not a genesis")
        if n > 1 and entry["kind"] == GENESIS:
            return bad("invalid", "structure", "second genesis")
        if n == 1:
            cid = entry["data"].get("chain_id")
            if not (isinstance(cid, str) and HEX32.fullmatch(cid)):
                return bad("invalid", "structure", "genesis chain_id is not 32 lowercase hex")
            chain.chain_id = cid
        if entry["seq"] != n - 1:
            return bad("tampered", "seq", f"seq is {entry['seq']}, expected {n - 1}")
    want_prev = chain.hashes[-1] if chain.hashes else ZEROS
    if entry["prev"] != want_prev:
        return bad("tampered", "prev", "prev is not 64 zeros" if n == 1 else f"prev does not match line {n - 1}")
    if (v1_hash(entry) if v1 else v0_hash(entry)) != entry["hash"]:
        return bad("tampered", "hash", "hash does not match the entry")
    if v1 and raw != canonical(entry):
        return bad("not canonical", "canonical", "stored bytes are not the canonical form")
    if not v1 and raw.decode("utf-8") != json.dumps(entry, sort_keys=True):
        return bad("not canonical", "canonical", "stored bytes are not the v0 line form")
    chain.hashes.append(entry["hash"])
    return None


def _head_record(data: bytes) -> dict | None:
    """A valid ledger.head: the canonical JSON of exactly chain_id, hash and seq, then one newline."""
    if not data.endswith(b"\n"):
        return None
    record, broken = _parse(data[:-1])
    if broken or set(record) != HEAD_KEYS:
        return None
    if not (isinstance(record["chain_id"], str) and HEX32.fullmatch(record["chain_id"])):
        return None
    if not (isinstance(record["hash"], str) and HEX64.fullmatch(record["hash"])):
        return None
    if not (_int(record["seq"]) and 0 <= record["seq"] <= MAX_INT):
        return None
    return record if data == canonical(record) + b"\n" else None


def verify_bytes(ledger: bytes, head: bytes | None = None) -> Verdict:
    """The ledger's bytes, and its ledger.head's when that file exists (None when it doesn't)."""
    end = ledger.rfind(b"\n") + 1
    complete, torn = ledger[:end], ledger[end:]
    lines = complete.split(b"\n")[:-1]
    chain = _Chain()
    for n, raw in enumerate(lines, start=1):
        problem = _line(chain, n, raw)
        if problem:
            return problem
    count = len(lines)
    last_seq = count - 1 if chain.version == 1 else None
    common = dict(version=chain.version, entries=count, head=chain.hashes[-1] if chain.hashes else None,
                  chain_id=chain.chain_id)
    if head is not None:
        record = _head_record(head)
        if record is None:
            return Verdict("invalid", "head", None, None, "ledger.head: not a valid head record", **common)
        h = record["seq"]
        if count and chain.version == 0:
            return Verdict("invalid", "head", None, h, "ledger.head: a v0 ledger has no chain id, so it can't have a head",
                           **common)
        if count and record["chain_id"] != chain.chain_id:
            return Verdict("invalid", "head", None, h, "ledger.head: chain_id does not match the ledger", **common)
        if not count:
            return Verdict("truncated", "head", None, h, f"ledger has no complete entries but ledger.head records seq {h}",
                           **common)
        if h <= last_seq and chain.hashes[h] != record["hash"]:
            return Verdict("tampered", "head", None, h, f"ledger.head: hash does not match seq {h}", **common)
        if h > last_seq:
            return Verdict("truncated", "head", None, h, f"ledger ends at seq {last_seq} but ledger.head records seq {h}",
                           **common)
    if torn:
        # after the last complete line, and its seq: line 0 and no seq when the genesis itself is torn
        return Verdict("torn tail", None, count, last_seq, f"{len(torn)} bytes after line {count}", **common)
    if not count:
        return Verdict("empty", None, None, None, "no entries", **common)
    return Verdict("intact", None, count, last_seq, "every rule holds", **common)


@contextmanager
def _polarizer_lock(folder: Path, wait: float):
    """Polarizer's own lock on its ledger, taken the way its verify takes it: only if the lock file
    exists, opened read-only, never created, tried every 50 ms for up to `wait` seconds. Held, no
    writer is halfway through a line. Where there is no fcntl (Windows), the read goes without it."""
    lock = folder / "ledger.jsonl.lock"
    if sys.platform == "win32" or not lock.exists():
        yield
        return
    import fcntl
    fd = os.open(lock, os.O_RDONLY)
    try:
        deadline = time.monotonic() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(f"{lock} stayed locked for {wait:g} s") from None
                time.sleep(0.05)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def read(ledger: Path, head: Path | None = None, lock_wait: float = 0.0) -> tuple[Verdict, bytes]:
    """Verify a ledger file, and the head file if it's given and exists. With lock_wait, Polarizer's
    lock beside the ledger is held while both are read. Returns the verdict and the bytes it judged."""
    ledger = Path(ledger)
    with _polarizer_lock(ledger.parent, lock_wait) if lock_wait else _no_lock():
        data = ledger.read_bytes()
        head_bytes = head.read_bytes() if head is not None and Path(head).exists() else None
    return verify_bytes(data, head_bytes), data


@contextmanager
def _no_lock():
    yield


def entries(data: bytes) -> list[dict]:
    """The complete lines of a ledger that verified, parsed."""
    end = data.rfind(b"\n") + 1
    return [json.loads(line) for line in data[:end].split(b"\n")[:-1]]


def target(path: Path, head: Path | None = None) -> tuple[Path, Path | None]:
    """What `parallax verify PATH` checks: a folder is Polarizer's layout (ledger.jsonl, and
    ledger.head when it's there); a file is checked alone, or with the head file named."""
    path = Path(path)
    if path.is_dir():
        return path / "ledger.jsonl", head or path / "ledger.head"
    return path, head
