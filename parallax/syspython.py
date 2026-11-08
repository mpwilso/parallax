"""The task's venv is built on the system Python, which the sandbox can read.

A venv doesn't copy its Python: bin/python links to the interpreter it was made from. The sandbox
denies reads of $HOME except a few folders, the venv among them (sandbox.py), so a venv made from a
Python under $HOME can't start inside it. uv prefers the Pythons it downloads, which live under
~/.local/share/uv/python: field-demo task 70d1f6's tests stopped with "python: command not found"
(2026-11-08), and Maker had worked around it with /usr/bin/python3. The sandbox's reads stay as they
are, a security setting; the venv moves instead.

- [build] setup runs with uv held to a system Python, never one it downloaded (UV_ENV). So
  `uv venv "$PARALLAX_VENV"` makes the venv on the system Python, and `uv venv --python
  /usr/bin/python3` (VENV_LINE, which Parallax's own setup lines use) says so outright. A setup that
  asks uv for another Python fails, or is caught below; it never quietly gets uv's.
- Before a Python setup runs: /usr/bin/python3 must be there, and meet the project's requires-python
  when pyproject.toml names one. Otherwise the task stops, saying what to install.
- After setup: the venv's Python must resolve to a path the sandbox can read. Otherwise the venv is
  removed and the task stops, saying so. A finished venv from before this check is made again on the
  next run, as you, by the same setup command (build.py).
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from .core import ParallaxError

PYTHON = "/usr/bin/python3"
UV_ENV = {"UV_PYTHON_PREFERENCE": "only-system", "UV_PYTHON_DOWNLOADS": "never"}  # uv's own settings, for setup only
VENV_LINE = f'uv venv --python {PYTHON} "$PARALLAX_VENV"'
PYTHON_TOOLS = {"uv", "uvx", "pip", "pip3", "python", "python3", "virtualenv", "poetry", "pdm", "hatch", "pipenv"}
CLAUSE = re.compile(r"^(~=|===|==|!=|<=|>=|<|>)\s*(\d+(?:\.\d+)*)(\.\*)?$")


class Unusable(ParallaxError):
    """The venv can't be made where the sandbox can run it. ran: the setup run, when setup ran."""

    def __init__(self, why: str, ran: subprocess.CompletedProcess | None = None):
        super().__init__(why)
        self.ran = ran


def install_line() -> str:
    return "xcode-select --install" if sys.platform == "darwin" else "sudo apt-get install -y python3 python3-venv"


def version(python: str | None = None) -> tuple[int, ...] | None:
    """The interpreter's version, or None when it isn't there or doesn't run."""
    python = python or PYTHON
    if not (os.path.isfile(python) and os.access(python, os.X_OK)):
        return None
    try:
        out = subprocess.run([python, "-I", "-c", "import sys; print(*sys.version_info[:3], sep='.')"],
                             capture_output=True, text=True, timeout=30)
        return tuple(int(x) for x in out.stdout.strip().split(".")) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def requires(repo: Path, commit: str) -> str:
    """The project's requires-python at commit, or "" when it names none."""
    out = subprocess.run(["git", "-C", str(repo), "show", f"{commit}:pyproject.toml"], capture_output=True)
    if out.returncode != 0:
        return ""
    try:
        project = tomllib.loads(out.stdout.decode("utf-8", "replace")).get("project")
    except tomllib.TOMLDecodeError:
        return ""
    value = project.get("requires-python") if isinstance(project, dict) else None
    return value.strip() if isinstance(value, str) else ""


def allows(spec: str, have: tuple[int, ...]) -> bool | None:
    """Whether a Python of version have meets a requires-python, read as PEP 440 reads the forms
    projects use. None when a clause is one this doesn't read: then it claims nothing."""
    for clause in (c.strip() for c in spec.split(",")):
        if not clause:
            continue
        m = CLAUSE.match(clause)
        if not m:
            return None
        op, text, star = m.groups()
        want = tuple(int(x) for x in text.split("."))
        if star:
            if op not in ("==", "!="):
                return None
            if (have[:len(want)] == want) != (op == "=="):
                return False
            continue
        n = max(len(want), len(have))
        a, b = have + (0,) * (n - len(have)), want + (0,) * (n - len(want))
        if op == "~=":
            if len(want) < 2:
                return None
            fits = a >= b and have[:len(want) - 1] == want[:-1]
        else:
            fits = {"==": a == b, "===": a == b, "!=": a != b, ">=": a >= b, "<=": a <= b, ">": a > b, "<": a < b}[op]
        if not fits:
            return False
    return True


def _wanted(spec: str) -> str:
    """What to install for a requires-python, in words."""
    for clause in spec.split(","):
        m = CLAUSE.match(clause.strip())
        if m and m.group(1) in (">=", "~=") and not m.group(3):
            return f"Python {m.group(2)} or newer"
    return f"a Python that meets {spec}"


def python_setup(command: str) -> bool:
    """The setup runs a Python tool by name, so it makes a Python venv."""
    from .tools import commands
    return bool(set(commands(command)) & PYTHON_TOOLS)


def before(command: str, repo: Path, commit: str) -> str:
    """Why a Python setup can't make its venv on the system Python, said before it runs: "" when it
    can, or when the setup isn't a Python one."""
    if not python_setup(command):
        return ""
    have = version()
    if have is None:
        return (f"the task's venv is built on the system Python, {PYTHON}, which the sandbox can read, and there is "
                f"none. install it with {install_line()}, then retry")
    spec = requires(repo, commit)
    if spec and allows(spec, have) is False:
        return (f"the project needs Python {spec} (requires-python in pyproject.toml), and the system Python, {PYTHON}, "
                f"is {'.'.join(map(str, have))}. the task's venv is built on it, since the sandbox can read it: install "
                f"{_wanted(spec)} as {PYTHON}, then retry")
    return ""


def interpreter(venv: Path) -> Path | None:
    """Where the venv's python really is, links followed, or None when it has none."""
    exe = Path(venv) / "bin" / "python"
    return Path(os.path.realpath(exe)) if os.path.lexists(exe) else None


def unreadable(venv: Path) -> str:
    """Why the sandbox couldn't start this venv's Python: "" when it can, or when the venv has none."""
    real = interpreter(venv)
    if real is None:
        return ""
    from .sandbox import Rules, denied_reads
    if Rules(allow_write=[], deny_write=[], deny_read=denied_reads(), allow_read=[str(venv)], domains=[]).can_read(real):
        return ""
    return (f"the task's venv runs {real}, which the sandbox can't read, so the plan's tests couldn't start Python. "
            f"Parallax builds the venv on the system Python and never widens the sandbox for it: make [build] setup "
            f"create it with {VENV_LINE}, then retry")
