"""Preflight: before every launch, test both layers with the build's own generated rules.

- Bash layer: srt runs a probe with the same rules the maker's sandbox gets. It tries to create
  a sentinel file inside each protected directory that exists (the shared .git directory among
  them), and writes each protected file in a mirror folder generated with the same rules, so no
  real protected file is touched. Any sentinel that got through is removed.
- Tool layer: the permission function is called directly with made-up inputs, for every
  protected path and every tool that writes, plus reads of the approval key and your login.
- Network: the probe tries a local port Parallax listens on (standing in for the UI) and a
  domain the plan doesn't list.
- Environment: the probe looks for keys and tokens in its environment, and tries to read the
  approval key and Claude's credential file.

Anything allowed, or any check that can't run, refuses the launch.
Preflight tests srt 1.0's copy of the rules. The maker's Bash runs in Claude Code's own copy of
the same sandbox runtime; preflight can't start that one without a model.
"""
from __future__ import annotations

import copy
import json
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import approvals, sandbox
from .agents.claude import tool_to_action
from .core import Project
from .gate import Scope, make_permission_fn
from .ledger import Ledger

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSW|CREDENTIAL|AUTH|COOKIE|SESSION", re.I)
VALUE_MARKS = ["sk-ant-", "ghp_", "gho_", "github_pat_", "xoxb-", "xoxp-", "AKIA", "-----BEGIN"]
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

PROBE = r'''
import json, os, socket, sys, urllib.request
spec = json.loads(sys.argv[1])
out = {"written": [], "readable": [], "network": [], "env": sorted(os.environ)}
out["env_values"] = [k for k, v in os.environ.items() if any(m in v for m in spec["value_marks"])]
for path in spec["sentinels"]:
    try:
        with open(path, "x") as f:
            f.write("x")
        out["written"].append(path)
    except OSError:
        pass
for path, is_dir in spec["files"]:
    target = os.path.join(path, "preflight-sentinel") if is_dir else path
    try:
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(target, "a") as f:
            f.write("x")
        out["written"].append(path)
    except OSError:
        pass
for path in spec["reads"]:
    try:
        with open(path, "rb") as f:
            f.read(1)
        out["readable"].append(path)
    except OSError:
        pass
if spec.get("port"):
    try:
        socket.create_connection(("127.0.0.1", spec["port"]), timeout=3).close()
        out["network"].append("127.0.0.1:%d" % spec["port"])
    except OSError:
        pass
for url in spec.get("urls", []):
    try:
        urllib.request.urlopen(url, timeout=10).close()
        out["network"].append(url)
    except Exception:
        pass
print("PREFLIGHT " + json.dumps(out))
'''


@dataclass
class Line:
    name: str
    detail: str
    ok: bool


Runner = Callable[[Path, Path, dict, dict], dict | None]  # (srt config, cwd, spec, env) -> probe output


def run_srt(config: Path, cwd: Path, spec: dict, env: dict) -> dict | None:
    """Run the probe under srt. None if it didn't report: the sandbox didn't start, fail closed."""
    if not shutil.which("srt", path=env.get("PATH")):
        return None
    cmd = f"python3 -c {shlex.quote(PROBE)} {shlex.quote(json.dumps(spec))}"
    try:
        out = subprocess.run(["srt", "--settings", str(config), "-c", cmd], cwd=cwd, env=env,
                             capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("PREFLIGHT "):
            return json.loads(line[len("PREFLIGHT "):])
    return None


def _mirror(worktree: Path, targets: list[Path], mirror: Path) -> list[tuple[Path, bool]]:
    """A folder with the worktree's protected layout: empty copies of what exists, nothing else."""
    if mirror.exists():
        shutil.rmtree(mirror)
    mirror.mkdir(parents=True)
    out = []
    for t in targets:
        m = mirror / t.relative_to(worktree)
        if t.is_dir():
            m.mkdir(parents=True, exist_ok=True)
        elif t.exists():
            m.parent.mkdir(parents=True, exist_ok=True)
            m.touch()
        out.append((m, t.is_dir()))
    return out


def bash_layer(worktree: Path, targets: list[Path], git_dir: Path, rules: sandbox.Rules, home: Path,
               env: dict, runner: Runner, extra: dict) -> tuple[dict | None, dict | None, list[str]]:
    """Two probe runs: sentinels in the real protected directories, then the mirror.

    Returns (real run output, mirror run output, sentinels that got through and were removed).
    """
    token = secrets.token_hex(4)
    real_dirs = [t for t in [*targets, git_dir] if t.is_dir()]
    sentinels = [str(d / f".parallax-preflight-{token}") for d in real_dirs]
    real_cfg = home / "preflight-srt.json"
    real_cfg.write_text(json.dumps(rules.srt()))
    real = runner(real_cfg, worktree, {"sentinels": sentinels, "files": [], **extra}, env)
    leaked = [s for s in sentinels if Path(s).exists()]
    for s in leaked:
        Path(s).unlink()

    mirror_root = home / "preflight-mirror"
    mirrored = _mirror(worktree, targets, mirror_root)
    sandbox.prepare_mount_points([m for m, _ in mirrored])
    files = [(m, d) for (m, d), t in zip(mirrored, targets) if not t.is_dir()]  # real dirs got sentinels
    m_rules = sandbox.rules(mirror_root, [m for m, _ in mirrored], git_dir=None, venv=None, reads=[], domains=[])
    m_cfg = home / "preflight-mirror-srt.json"
    m_cfg.write_text(json.dumps(m_rules.srt()))
    mirror = runner(m_cfg, mirror_root, {"sentinels": [], "files": [[str(m), d] for m, d in files],
                                         "reads": [], "value_marks": []}, env)
    shutil.rmtree(mirror_root, ignore_errors=True)
    return real, mirror, leaked


def tool_layer(project: Project, task_id: str, worktree: Path, targets: list[Path], scope: Scope,
               secret_reads: list[Path]) -> tuple[int, int]:
    """(allowed, tried): the permission function against every protected path and writing tool.

    It records into a throwaway ledger, so preflight leaves no trace but its summary."""
    with tempfile.TemporaryDirectory() as tmp:
        probe = copy.copy(project)
        probe.ledger = Ledger(Path(tmp) / "ledger.jsonl")
        fn = make_permission_fn(probe, task_id, worktree, scope=scope)
        tried = allowed = 0
        for t in targets:
            path = str(t / "x") if t.is_dir() else str(t)
            calls = [(tool, {"file_path": path, "notebook_path": path, "content": "x", "old_string": "",
                             "new_string": "x", "edits": []}) for tool in WRITE_TOOLS]
            calls.append(("Bash", {"command": f"echo x > {shlex.quote(str(t))}"}))
            for tool, args in calls:
                tried += 1
                allowed += fn(*tool_to_action(tool, args)).allowed
        for path in secret_reads:
            tried += 1
            allowed += fn(*tool_to_action("Read", {"file_path": str(path)})).allowed
    return allowed, tried


def run(project: Project, task_id: str, worktree: Path, rules: sandbox.Rules, home: Path, scope: Scope,
        env: dict, runner: Runner = run_srt) -> list[Line]:
    targets = sandbox.protected_targets(worktree)
    git_dir = sandbox.shared_git_dir(worktree)
    secret_reads = [approvals.key_path(), Path.home() / ".claude" / ".credentials.json"]

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    urls = [] if any(d in ("example.com", "*.com", "*") for d in rules.domains) else ["http://example.com/"]
    try:
        extra = {"reads": [str(p) for p in secret_reads], "port": port, "urls": urls,
                 "value_marks": VALUE_MARKS}
        real, mirror, leaked = bash_layer(worktree, targets, git_dir, rules, home, env, runner, extra)
    finally:
        listener.close()

    lines = []
    total = len(targets) + 1  # every protected path, and the shared .git directory
    if real is None or mirror is None:
        lines.append(Line("bash layer", "the sandbox didn't run the probe", False))
    else:
        writable = len(set(real["written"]) | set(leaked)) + len(mirror["written"])
        lines.append(Line("bash layer", f"{writable} of {total} protected paths writable", writable == 0))

    allowed, tried = tool_layer(project, task_id, worktree, targets + [git_dir], scope, secret_reads)
    lines.append(Line("tool layer", f"{allowed} of {tried} protected writes and reads allowed", allowed == 0))

    if real is None:
        lines.append(Line("network", "not tested: the sandbox didn't run", False))
    else:
        reached = real["network"]
        what = "blocked, including the ui port" if not rules.domains else "only the plan's domains, not the ui port"
        lines.append(Line("network", what if not reached else f"reached {', '.join(reached)}", not reached))

    if real is None:
        lines.append(Line("environment", "not tested: the sandbox didn't run", False))
    else:
        # the sandbox's own proxy settings (HTTP_PROXY, CLOUDSDK_PROXY_PASSWORD...) aren't your secrets
        names = [k for k in real["env"] if SECRET_NAME.search(k) and "PROXY" not in k.upper()] + real["env_values"]
        leaks = names + real["readable"]
        lines.append(Line("environment", "no keys or tokens visible" if not leaks
                          else f"visible: {', '.join(sorted(set(leaks)))}", not leaks))

    project.ledger.append("preflight.recorded", "parallax", "; ".join(f"{line.name}: {line.detail}" for line in lines),
                          task=task_id, ok=all(line.ok for line in lines))
    return lines


def report(lines: list[Line]) -> list[str]:
    width = max([34] + [len(line.detail) + 2 for line in lines])
    out = [f"{line.name:<13}{line.detail:<{width}}{'ok' if line.ok else 'fail'}" for line in lines]
    out.append("ready to launch." if all(line.ok for line in lines) else "not ready: refusing to launch.")
    return out
