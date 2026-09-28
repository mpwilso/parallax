"""Append-only ledger.

Every event is one JSON line. Each line carries the hash of the line before it,
so editing or deleting history breaks the chain and `verify()` catches it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

GENESIS = "0" * 64


def _hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

    def entries(self) -> list[dict]:
        with self.path.open() as f:
            return [json.loads(line) for line in f if line.strip()]

    def _last_hash(self) -> str:
        entries = self.entries()
        return entries[-1]["hash"] if entries else GENESIS

    def append(self, kind: str, actor: str, reason: str = "", **data) -> dict:
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
