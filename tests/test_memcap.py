"""The memory cap: a runaway fails its own run with a plain message instead of taking the machine down.
Seen on 2026-09-30, when a test Reticle wrote looped forever on boltons-319 and WSL ran out of memory."""
import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from parallax import build, gate, memcap, testrun
from parallax.agents.claude import rule_on_tool_call
from sandboxcheck import why_not

pytestmark = pytest.mark.skipif(not memcap.available(), reason="the cap reads /proc")

MB = 1024 ** 2
# the shape of the boltons-319 test: a loop that never ends, filling a list
RUNAWAY = "x = []\nwhile True:\n    x.append(b'x' * 10_000_000)\n"
ROOT = Path(__file__).resolve().parent.parent


def test_a_runaway_is_stopped_at_the_limit_with_a_plain_message():
    start = time.monotonic()
    r = memcap.run([sys.executable, "-c", RUNAWAY], 150 * MB, what="the tests", capture=True)
    assert r.over and r.code == memcap.EXIT and r.peak > 150 * MB
    last = r.output.strip().splitlines()[-1]
    assert last.startswith("memory cap: the tests used ") and last.endswith(", over the 0.15 GB limit, so it was stopped")
    assert time.monotonic() - start < 30


def test_the_cap_counts_what_the_command_starts():
    child = f"import subprocess, sys; subprocess.run([sys.executable, '-c', {RUNAWAY!r}])"
    r = memcap.run([sys.executable, "-c", child], 150 * MB, capture=True)
    assert r.over and r.code == memcap.EXIT


def test_under_the_limit_the_command_runs_as_it_would_uncapped():
    r = memcap.run([sys.executable, "-c", f"import os, sys; print(os.environ['{memcap.ENV}']); sys.exit(3)"],
                   memcap.GB, capture=True)
    assert (r.code, r.output.strip(), r.over, r.timed_out) == (3, str(memcap.GB), False, False)
    r = memcap.run([sys.executable, "-c", "import time; time.sleep(30)"], memcap.GB, timeout=0.5, capture=True)
    assert r.timed_out and r.code == 124 and not r.over


def test_the_command_line_form_fails_the_run_and_says_why():
    ok = subprocess.run([sys.executable, "-m", "parallax.memcap", "1G", "--", sys.executable, "-c", "print('hi')"],
                        capture_output=True, text=True, cwd=ROOT)
    assert (ok.returncode, ok.stdout, ok.stderr) == (0, "hi\n", "")
    bad = subprocess.run([sys.executable, "-m", "parallax.memcap", "150M", "--", sys.executable, "-c", RUNAWAY],
                         capture_output=True, text=True, cwd=ROOT)
    assert bad.returncode == memcap.EXIT
    assert bad.stderr.startswith("memory cap: the command used ") and "over the 0.15 GB limit, so it was stopped" in bad.stderr


def test_the_guard_caps_the_process_it_runs_in_and_what_it_starts():
    code = ("import subprocess, sys\nfrom parallax import memcap\nmemcap.guard(150 * 1024 ** 2, 'the eval run')\n"
            f"subprocess.run([sys.executable, '-c', {RUNAWAY!r}])\nprint('not stopped')")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=ROOT, timeout=60)
    assert r.returncode == memcap.EXIT and "not stopped" not in r.stdout
    assert r.stderr.startswith("memory cap: the eval run used")


def test_a_repos_runaway_test_is_recorded_as_tests_that_couldnt_run(tmp_path, monkeypatch):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "srt").write_text(f"#!{sys.executable}\n{RUNAWAY}")
    (fake / "srt").chmod(0o755)
    monkeypatch.setattr(memcap, "COMMAND", 150 * MB)
    code, out = testrun._srt(tmp_path / "cfg.json", tmp_path, "pytest", {"PATH": f"{fake}:/usr/bin:/bin"})
    assert code == memcap.EXIT and not testrun.Results(code).ran  # yours to look at, never the maker's to fix
    assert out.strip().splitlines()[-1].startswith("memory cap: the tests used")


def test_this_suite_runs_under_the_4_gb_cap():
    """scripts/test.sh runs pytest under the cap, and CI runs scripts/test.sh. Run straight from
    pytest, outside CI, there's nothing to check."""
    if not (os.environ.get("PARALLAX_TEST_SH") or os.environ.get("CI")):
        pytest.skip("run through scripts/test.sh to check the memory cap")
    assert os.environ.get(memcap.ENV) == str(4 * memcap.GB) == str(memcap.SUITE)
    watcher, pid = int(os.environ[memcap.ENV_PID]), os.getpid()
    while pid not in (watcher, 0, 1):
        pid = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
    assert pid == watcher, "the process watching the cap isn't above this one"


# the agents' own commands --------------------------------------------------------------------------

def test_every_command_the_maker_runs_is_under_the_cap_and_the_sandbox_can_read_it(repo):
    from test_guard_and_checker import setup
    proj, tid, wt = setup(repo)
    p = build.prepare(proj, tid, setup=False, launching=False)
    folder = p.home / memcap.FOLDER
    assert (folder / "memcap.py").read_bytes() == Path(memcap.__file__).read_bytes()
    assert str(folder) in json.loads(p.settings.read_text())["sandbox"]["filesystem"]["allowRead"]
    assert str(folder) not in p.rules.allow_write

    fn = gate.make_permission_fn(proj, tid, wt, scope=p.scope)
    ruled = fn("shell.run", "pytest -q")
    assert ruled.allowed and ruled.command == memcap.shell("pytest -q", folder) != "pytest -q"
    hook = asyncio.run(rule_on_tool_call(fn, "Bash", {"command": "pytest -q", "timeout": 5000}))["hookSpecificOutput"]
    assert hook["permissionDecision"] == "allow" and hook["updatedInput"] == {"command": ruled.command, "timeout": 5000}
    assert "updatedInput" not in asyncio.run(rule_on_tool_call(fn, "Read", {"file_path": "a.py"}))["hookSpecificOutput"]
    assert [e["data"]["key"] for e in proj.ledger.entries() if e["kind"] == "action.granted"][-2] == "pytest -q"  # recorded as asked

    # the command does what it says: the same shell command, and a runaway stops with the cap's line
    same = subprocess.run(["bash", "-c", memcap.shell("cd /; echo \"it's $((1 + 2)) in $PWD\"; exit 4", folder)],
                          capture_output=True, text=True, timeout=60)
    assert (same.returncode, same.stdout, same.stderr) == (4, "it's 3 in /\n", "")
    runaway = memcap.shell(f"python3 -c {shlex.quote(RUNAWAY)}", folder, 150 * MB)
    r = subprocess.run(["bash", "-c", runaway], capture_output=True, text=True, timeout=60)
    assert r.returncode == memcap.EXIT
    assert r.stderr.strip().splitlines()[-1].startswith("memory cap: the command used ")


def claude_code_runs(command: str, cwd: Path, srt: Path | None = None) -> tuple[int, str, Path]:
    """One Bash call the way Claude Code makes it: the command in a shell started in the working
    directory, then that shell's `pwd -P` kept as the next call's working directory."""
    script = f"{command}\n__rc=$?; pwd -P >&2; exit $__rc"
    argv = ["srt", "--settings", str(srt), "-c", script] if srt else ["bash", "-c", script]
    r = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120, env=build.scrubbed_env(None))
    return r.returncode, r.stdout, Path(r.stderr.strip().splitlines()[-1])


def test_a_cd_in_one_maker_command_carries_over_to_the_next_as_it_did_uncapped(tmp_path):
    folder = memcap.place(tmp_path / "home")
    work = tmp_path / "wt"
    (work / "sub").mkdir(parents=True)
    (work / "sub" / "data.txt").write_text("in sub\n")
    for wrap in (lambda c: c, lambda c: memcap.shell(c, folder)):  # uncapped, then capped: the same
        code, _, cwd = claude_code_runs(wrap("cd sub"), work)
        assert code == 0 and cwd == (work / "sub").resolve()
        code, out, cwd = claude_code_runs(wrap("cat data.txt"), cwd)
        assert (code, out) == (0, "in sub\n")
        code, out, cwd = claude_code_runs(wrap("cd .. && (exit 3)"), cwd)
        assert code == 3 and cwd == work.resolve()  # the exit code and the cd, both kept


@pytest.mark.skipif(bool(why_not()), reason=str(why_not()))
def test_in_the_real_sandbox_the_makers_capped_command_runs_and_stops_a_runaway(repo, monkeypatch):
    import tempfile
    from test_guard_and_checker import setup
    missing = Path(tempfile.mkdtemp(prefix="px", dir="/tmp")) / "t"  # short: srt's sockets live in it
    monkeypatch.setenv("CLAUDE_CODE_TMPDIR", str(missing))
    proj, tid, wt = setup(repo)
    p = build.prepare(proj, tid, setup=False, launching=False)
    assert missing.is_dir() and str(missing) in p.rules.allow_write  # made before the first command
    folder, srt = p.home / memcap.FOLDER, p.home / "srt.json"  # the same rules as the maker's settings
    env = {**build.scrubbed_env(None), "CLAUDE_CODE_TMPDIR": str(missing)}
    ok = subprocess.run(["srt", "--settings", str(srt), "-c", memcap.shell("echo ran", folder)], cwd=wt, env=env,
                        capture_output=True, text=True, timeout=120)
    assert ok.returncode == 0 and ok.stdout.strip().endswith("ran"), ok.stderr
    runaway = memcap.shell(f"python3 -c {shlex.quote(RUNAWAY)}", folder, 150 * MB)
    r = subprocess.run(["srt", "--settings", str(srt), "-c", runaway], cwd=wt, env=env, capture_output=True, text=True,
                       timeout=120)
    assert r.returncode == memcap.EXIT and "memory cap: the command used " in r.stderr, r.stderr[-400:]

    # Maker's working directory carries over between commands, as it did before the cap. The capped
    # command notes it in a file in the sandbox's TMPDIR, which the build makes if it's missing: here a
    # new one, so the shared /tmp/claude is never touched
    (wt / "sub").mkdir()
    (wt / "sub" / "data.txt").write_text("in sub\n")
    code, _, cwd = claude_code_runs(memcap.shell("cd sub", folder), wt, srt)
    assert code == 0 and cwd == (wt / "sub").resolve()
    code, out, _ = claude_code_runs(memcap.shell("cat data.txt", folder), cwd, srt)
    assert code == 0 and out.strip().endswith("in sub")


def test_focus_and_reticle_run_no_commands_so_there_is_nothing_to_cap():
    from parallax.agents import claude
    assert "Bash" not in claude.PLAN_TOOLS  # plan, draft and reticle stages get only these


def test_the_sandboxs_temp_folder_is_made_before_makers_first_command(repo, monkeypatch, tmp_path):
    for var in ("CLAUDE_CODE_TMPDIR", "CLAUDE_TMPDIR"):
        monkeypatch.delenv(var, raising=False)
    assert memcap.sandbox_tmp() == Path("/tmp/claude")  # srt's own, when nothing overrides it
    assert memcap.sandbox_tmp({"CLAUDE_TMPDIR": "/a", "CLAUDE_CODE_TMPDIR": "/b"}) == Path("/b")
    missing = tmp_path / "gone" / "claude"
    monkeypatch.setenv("CLAUDE_TMPDIR", str(missing))
    from test_guard_and_checker import setup
    proj, tid, _ = setup(repo)
    p = build.prepare(proj, tid, setup=False, launching=False)
    assert missing.is_dir() and str(missing) in p.rules.allow_write
    monkeypatch.delenv("CLAUDE_TMPDIR")
    assert memcap.ensure_tmp() == Path("/tmp/claude") and Path("/tmp/claude").is_dir()  # made if missing, left if not
