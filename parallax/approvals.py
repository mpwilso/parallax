"""Approval signatures: a gate counts only if it was signed with the approval key.

The key lives in ~/.config/parallax/key, outside every worktree; `parallax doctor` creates it.
A signature covers the task, the gate, and the hash of every approved file, so it can't be
moved to another task, another gate, or edited files. From M9 the sandbox can't read the key.
A missing or unreadable key fails closed: nothing can be approved, and no approval counts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path

from .core import ParallaxError


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def key_path() -> Path:
    return config_home() / "parallax" / "key"


def load_key(path: Path | None = None) -> bytes:
    path = path or key_path()
    if not path.exists():
        raise ParallaxError("no approval key. run parallax doctor to create it")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ParallaxError(f"the approval key is readable by others. run chmod 600 {path}")
    key = path.read_bytes().strip()
    if len(key) < 32:
        raise ParallaxError("the approval key is too short. delete it and run parallax doctor")
    return key


def _message(task: str, gate: str, files: dict[str, str]) -> bytes:
    return json.dumps({"task": task, "gate": gate, "files": files}, sort_keys=True).encode()


def sign(key: bytes, task: str, gate: str, files: dict[str, str]) -> str:
    return hmac.new(key, _message(task, gate, files), hashlib.sha256).hexdigest()


def valid(key: bytes | None, data: dict) -> bool:
    """True if a gate.approved entry's signature matches what it claims."""
    if key is None or not isinstance(data.get("sig"), str):
        return False
    try:
        expected = sign(key, data["task"], data["gate"], data["files"])
    except (KeyError, TypeError):
        return False
    return hmac.compare_digest(expected, data["sig"])


def key_or_none() -> bytes | None:
    """For reading state: with no usable key, no approval counts."""
    try:
        return load_key()
    except (ParallaxError, OSError):
        return None
