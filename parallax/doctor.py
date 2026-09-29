"""`parallax doctor`: is this machine ready to run a maker in a sandbox?

A missing sandbox is a failure: Parallax refuses to launch without one. Windows interop, the
Windows PATH, or mounted Windows drives are warnings: they widen what a process in WSL can reach,
but the sandbox still stands. The approval key is created here if it's missing.

Every probe is a field on Machine, so tests can describe a machine without touching this one.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .approvals import key_path

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
HARDENING = "docs/wsl.md#harden-wsl"


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _run(argv: list[str]) -> str | None:
    """A command's stdout, or None if it couldn't run or failed."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


@dataclass
class Machine:
    platform: str = field(default_factory=lambda: sys.platform)
    read: Callable[[str], str] = _read
    exists: Callable[[str], bool] = os.path.exists
    which: Callable[[str], str | None] = shutil.which
    run: Callable[[list[str]], str | None] = _run
    path: str = field(default_factory=lambda: os.environ.get("PATH", ""))
    key: Path = field(default_factory=key_path)


@dataclass
class Check:
    name: str
    detail: str
    status: str
    hint: str = ""


def _home(path: Path | str) -> str:
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home):] if text == home or text.startswith(home + os.sep) else text


def is_wsl(m: Machine) -> bool:
    return m.platform == "linux" and "microsoft" in m.read("/proc/sys/kernel/osrelease").lower()


def check_platform(m: Machine) -> Check:
    if m.platform == "darwin":
        return Check("platform", "macos", OK)
    if m.platform != "linux":
        return Check("platform", m.platform, FAIL, "no sandbox on native windows. run parallax inside wsl2")
    if not is_wsl(m):
        return Check("platform", "linux", OK)
    if "wsl2" in m.read("/proc/sys/kernel/osrelease").lower():
        return Check("platform", "linux on wsl2", OK)
    return Check("platform", "linux on wsl1", FAIL, "the sandbox needs wsl2: wsl --set-version <distro> 2")


def check_sandbox(m: Machine) -> Check:
    tools = {"bubblewrap": "bwrap", "socat": "socat", "srt": "srt"} if m.platform == "linux" else {"srt": "srt"}
    missing = [name for name, binary in tools.items() if not m.which(binary)]
    if missing:
        return Check("sandbox", f"missing {', '.join(missing)}", FAIL, "see the install steps in README.md")
    return Check("sandbox", ", ".join(tools), OK)


def check_login(m: Machine) -> Check:
    if not m.which("claude"):
        return Check("claude login", "claude not found", FAIL, "install claude code, see README.md")
    out = m.run(["claude", "auth", "status"])
    try:
        logged_in = bool(json.loads(out or "").get("loggedIn"))
    except (json.JSONDecodeError, AttributeError):
        return Check("claude login", "couldn't read claude auth status", FAIL, "run claude and log in")
    if not logged_in:
        return Check("claude login", "not logged in", FAIL, "run claude and log in")
    return Check("claude login", "found", OK)


def _drives_mounted(mounts: str) -> bool:
    for line in mounts.splitlines():
        parts = line.split()
        if len(parts) >= 3 and re.fullmatch(r"/mnt/[a-zA-Z]", parts[1]) and parts[2] in ("9p", "drvfs"):
            return True
    return False


def check_windows(m: Machine) -> Check:
    if not is_wsl(m):
        return Check("windows", "not wsl", OK)
    interop = m.exists("/proc/sys/fs/binfmt_misc/WSLInterop") or m.exists("/proc/sys/fs/binfmt_misc/WSLInterop-late")
    path = any(re.match(r"/mnt/[a-zA-Z](/|$)", p) for p in m.path.split(os.pathsep))
    drives = _drives_mounted(m.read("/proc/mounts"))
    detail = ", ".join(f"{name} {'on' if on else 'off'}" for name, on in
                       (("interop", interop), ("path", path), ("drives", drives)))
    if interop or path or drives:
        return Check("windows", detail, WARN, f"see {HARDENING}")
    return Check("windows", detail, OK)


def check_approval_key(m: Machine) -> Check:
    """Create the key if it's missing. Refuse a key others can read."""
    key = m.key
    if not key.exists():
        key.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(key.parent, 0o700)
        fd = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32) + "\n")
        return Check("approval key", f"created {_home(key)}", OK)
    if stat.S_IMODE(key.stat().st_mode) & 0o077:
        return Check("approval key", f"{_home(key)} is readable by others", FAIL, f"chmod 600 {_home(key)}")
    return Check("approval key", _home(key), OK)


def check_signing_key(m: Machine) -> Check:
    key = (m.run(["git", "config", "--get", "user.signingkey"]) or "").strip()
    if not key:
        return Check("signing key", "none: accept commits won't be signed", INFO)
    return Check("signing key", _home(key), OK)


CHECKS = (check_platform, check_sandbox, check_login, check_windows, check_approval_key, check_signing_key)


def run(m: Machine | None = None) -> list[Check]:
    m = m or Machine()
    return [c(m) for c in CHECKS]


def report(checks: list[Check]) -> list[str]:
    """One aligned line per check. Info lines have no status."""
    width = max([28] + [len(c.detail) + 2 for c in checks if c.status != INFO])
    lines = []
    for c in checks:
        if c.status == INFO:
            lines.append(f"{c.name:<14}{c.detail}")
        else:
            status = f"{c.status}: {c.hint}" if c.hint and c.status != OK else c.status
            lines.append(f"{c.name:<14}{c.detail:<{width}}{status}")
    return lines
