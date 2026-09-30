"""The memory cap: a runaway fails its own run with a plain message instead of taking the machine down.
Seen on 2026-09-30, when a test Reticle wrote looped forever on boltons-319 and WSL ran out of memory."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from parallax import memcap, testrun

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
    assert bad.stderr.startswith("memory cap: python") and "over the 0.15 GB limit, so it was stopped" in bad.stderr


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
    monkeypatch.setattr(memcap, "TEST_RUN", 150 * MB)
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
