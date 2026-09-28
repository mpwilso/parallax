"""Parallax runs the plan's tests itself. The maker's own report of passing tests doesn't count.

The tests run on a plain copy of the reviewed tree, with the test harness (conftest files, pytest
and tox settings, package scripts) put back to the base branch's version, so the maker can't
change how its own work is tested. They run in the sandbox, with the same kind of rules as the
build: writes only in the copy, no reads of $HOME except the copy and the task's venv, network
only to the plan's domains.

Exit codes follow pytest: 0 passed, 1 some failed. Anything else means the tests couldn't run,
which is not the maker's to fix, so it goes to you instead of back to the maker.
"""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import sandbox, tree

HARNESS = ("conftest.py", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
           "package.json", "noxfile.py")


@dataclass
class Results:
    exit: int
    per_file: dict[str, list[int]] = field(default_factory=dict)  # file -> [passed, counted, skipped]
    tail: str = ""

    @property
    def passed(self) -> int:
        return sum(v[0] for v in self.per_file.values())

    @property
    def total(self) -> int:
        return sum(v[1] for v in self.per_file.values())

    @property
    def ran(self) -> bool:
        return self.exit in (0, 1)

    @property
    def ok(self) -> bool:
        return self.exit == 0 and self.passed == self.total


def harness_from_base(worktree: Path, base: str, reviewed: str, copy: Path) -> list[str]:
    """Put every harness file back to the base branch's version. Returns what was changed."""
    in_base = set(tree.files_in(worktree, base))
    changed = []
    for rel in sorted(set(tree.files_in(worktree, reviewed)) | in_base):
        if PurePosixPath(rel).name not in HARNESS:
            continue
        target = copy / rel
        old = tree.show_file(worktree, base, rel) if rel in in_base else None
        now = target.read_bytes() if target.exists() else None
        if old == now:
            continue
        if old is None:
            target.unlink()
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(old)
        changed.append(rel)
    return changed


def _file_of(classname: str, file_attr: str | None, tests: list[str]) -> str:
    if file_attr:
        return file_attr
    for t in tests:
        path = t.split("::", 1)[0]
        mod = path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")
        if classname == mod or classname.startswith(mod + "."):
            return path
    return classname


def parse_junit(path: Path, tests: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {t.split("::", 1)[0]: [0, 0, 0] for t in tests}
    if not path.exists():
        return out
    for case in ET.parse(path).getroot().iter("testcase"):
        f = _file_of(case.get("classname", ""), case.get("file"), tests)
        row = out.setdefault(f, [0, 0, 0])
        if case.find("skipped") is not None:
            row[2] += 1
            continue
        row[1] += 1
        if case.find("failure") is None and case.find("error") is None:
            row[0] += 1
    return out


def run(worktree: Path, base: str, reviewed: str, plan: dict, home: Path, venv: Path | None, env: dict,
        command: str, runner=None) -> tuple[Results, list[str]]:
    """Run the plan's tests on a copy of the reviewed tree. Returns (results, harness files reset)."""
    copy = home / "check-copy"
    shutil.rmtree(copy, ignore_errors=True)
    tree.export(worktree, reviewed, copy)
    reset = harness_from_base(worktree, base, reviewed, copy)
    tmp = copy / ".parallax-tmp"
    tmp.mkdir()
    junit = tmp / "junit.xml"
    tests = list(plan["tests"])
    cmd = command.format(junit=shlex.quote(str(junit)), tests=" ".join(shlex.quote(t) for t in tests))

    targets = sandbox.protected_targets(copy)
    sandbox.prepare_mount_points(targets)
    reads = [str(Path(r).expanduser()) for r in plan["outside_reads"]]
    rules = sandbox.rules(copy, targets, git_dir=None, venv=venv, reads=reads, domains=plan["domains"])
    cfg = home / "tests-srt.json"
    cfg.write_text(json.dumps(rules.srt()))
    # the tests' TMPDIR is set inside the sandbox: srt keeps its own short one for its sockets,
    # which break on long paths (Unix socket paths top out near 108 characters)
    code, output = (runner or _srt)(cfg, copy, f"export TMPDIR={shlex.quote(str(tmp))}; {cmd}", env)
    results = Results(code, parse_junit(junit, tests), "\n".join(output.strip().splitlines()[-15:]))
    shutil.rmtree(copy, ignore_errors=True)
    return results, reset


def _srt(config: Path, cwd: Path, cmd: str, env: dict) -> tuple[int, str]:
    if not shutil.which("srt", path=env.get("PATH")):
        return 127, "srt isn't installed, so the tests can't run in the sandbox"
    try:
        out = subprocess.run(["srt", "--settings", str(config), "-c", cmd], cwd=cwd, env=env,
                             capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return 124, "the tests ran past 30 minutes"
    return out.returncode, out.stdout + out.stderr

