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
import re
import shlex
import shutil
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from . import installs, memcap, sandbox, tree

HARNESS = ("conftest.py", "pytest.ini", ".pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml",
           "package.json", "noxfile.py")


@dataclass
class Results:
    exit: int
    per_file: dict[str, list[int]] = field(default_factory=dict)  # file -> [passed, counted, skipped]
    tail: str = ""
    reported: bool = True  # the run wrote its JUnit report. without one, exit 1 may just mean no pytest
    cases: list[dict] = field(default_factory=list)  # each test: file, name, outcome, and its message's first line
    output: str = ""  # everything the run printed: kept as a file when it fails (outputs.py)

    @property
    def passed(self) -> int:
        return sum(v[0] for v in self.per_file.values())

    @property
    def total(self) -> int:
        return sum(v[1] for v in self.per_file.values())

    @property
    def ran(self) -> bool:
        return self.exit in (0, 1) and self.reported

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
    """The test file a JUnit case came from, as a path: never a dotted module name."""
    if file_attr:
        return file_attr
    for t in tests:
        path = t.split("::", 1)[0]
        if path.endswith(".py"):
            mod = path[:-3].replace("/", ".")
            if classname == mod or classname.startswith(mod + "."):
                return path
    parts = classname.split(".")  # tests.test_m11.TestX -> tests/test_m11.py, the last lowercase module
    for n in range(len(parts), 0, -1):
        if parts[n - 1].startswith("test"):
            return "/".join(parts[:n]) + ".py"
    return classname.replace(".", "/") + ".py"


def parse_junit(path: Path, tests: list[str]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {t.split("::", 1)[0]: [0, 0, 0] for t in tests if t.split("::", 1)[0].endswith(".py")}
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


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_cases(path: Path, tests: list[str]) -> list[dict]:
    """Each test in a JUnit report: its outcome (pass, fail, error, skip) and the first line of its
    message, colour codes stripped. A collection error is one case with outcome error."""
    if not path.exists():
        return []
    out = []
    for case in ET.parse(path).getroot().iter("testcase"):
        found = {ch.tag: ch for ch in case}
        tag = next((t for t in ("error", "failure", "skipped") if t in found), None)
        outcome = {"error": "error", "failure": "fail", "skipped": "skip", None: "pass"}[tag]
        message = ANSI.sub("", found[tag].get("message") or "") if tag else ""
        out.append({"file": _file_of(case.get("classname", ""), case.get("file"), tests), "name": case.get("name", ""),
                    "classname": case.get("classname", ""),
                    "outcome": outcome, "message": (message.strip().splitlines() or [""])[0][:300]})
    return out


def run(worktree: Path, base: str, reviewed: str, plan: dict, home: Path, venv: Path | None, env: dict,
        command: str, runner=None, overlay: dict[str, bytes] | None = None) -> tuple[Results, list[str]]:
    """Run the plan's tests on a copy of the reviewed tree. Returns (results, harness files reset).

    overlay: files written over the copy after the harness reset, such as an eval's hidden tests."""
    copy = home / "check-copy"
    shutil.rmtree(copy, ignore_errors=True)
    tree.export(worktree, reviewed, copy)
    reset = harness_from_base(worktree, base, reviewed, copy)
    for rel, data in {**installs.files(venv), **(overlay or {})}.items():
        (copy / rel).parent.mkdir(parents=True, exist_ok=True)
        (copy / rel).write_bytes(data)
    # the tests' temp folder sits beside the copy, never inside it: a test that walks up from its
    # temp folder must not find the repo it's testing (seen in M12)
    tmp = home / "check-tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    junit = tmp / "junit.xml"
    tests = list(plan["tests"])
    cmd = command.format(junit=shlex.quote(str(junit)), tests=" ".join(shlex.quote(t) for t in tests))

    targets = sandbox.protected_targets(copy)
    sandbox.prepare_mount_points(targets)
    reads = [str(Path(r).expanduser()) for r in plan["outside_reads"]]
    rules = sandbox.rules(copy, targets, git_dir=None, venv=venv, reads=reads, domains=plan["domains"])
    rules.allow_write.append(str(tmp))
    rules.allow_read.append(str(tmp))
    from .uitest import installed  # the UI tester's pinned browser, for a repo's own browser tests
    tools = installed()
    if tools:
        rules.allow_read.append(str(tools.dir))
        env = {**env, "PARALLAX_BROWSER": str(tools.exe), **({"LD_LIBRARY_PATH": str(tools.lib)} if tools.lib else {})}
    env = {**env, **installs.env(venv, copy)}
    cfg = home / "tests-srt.json"
    cfg.write_text(json.dumps(rules.srt()))
    # the tests' TMPDIR is set inside the sandbox: srt keeps its own short one for its sockets,
    # which break on long paths (Unix socket paths top out near 108 characters)
    code, output = (runner or _srt)(cfg, copy, f"export TMPDIR={shlex.quote(str(tmp))}; {cmd}", env)
    results = Results(code, parse_junit(junit, tests), "\n".join(output.strip().splitlines()[-15:]), junit.exists(),
                      parse_cases(junit, tests), output)
    shutil.rmtree(copy, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return results, reset


def _srt(config: Path, cwd: Path, cmd: str, env: dict) -> tuple[int, str]:
    if not shutil.which("srt", path=env.get("PATH")):
        return 127, "srt isn't installed, so the tests can't run in the sandbox"
    # capped: a test that never ends can fill memory long before the timeout (seen on boltons-319)
    out = memcap.run(["srt", "--settings", str(config), "-c", cmd], memcap.COMMAND, what="the tests",
                     timeout=1800, capture=True, cwd=cwd, env=env)
    if out.timed_out:
        return 124, "the tests ran past 30 minutes"
    return out.code, out.output

