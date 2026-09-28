import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from parallax import doctor
from parallax.cli import main
from parallax.core import POLICY_FILE, STATE_DIR, ParallaxError, Project
from parallax.runner import flag_stale_runs

WSL2 = "6.18.33.2-microsoft-standard-WSL2"
HARDENED_MOUNTS = "none /mnt/wsl tmpfs rw 0 0\n"


def machine(tmp_path, platform="linux", osrelease=WSL2, tools=("bwrap", "socat", "srt", "claude"),
            files=(), mounts=HARDENED_MOUNTS, path="/usr/bin:/bin", logged_in=True, signing=""):
    """A made-up machine: hardened WSL2 with everything installed, unless told otherwise."""
    text = {"/proc/sys/kernel/osrelease": osrelease, "/proc/mounts": mounts}

    def run(argv):
        if argv[:3] == ["claude", "auth", "status"]:
            return json.dumps({"loggedIn": logged_in})
        if argv[:2] == ["git", "config"]:
            return signing or None
        return None

    return doctor.Machine(platform=platform, read=lambda p: text.get(p, ""), exists=lambda p: p in files,
                          which=lambda b: f"/usr/bin/{b}" if b in tools else None, run=run, path=path,
                          key=tmp_path / "config" / "parallax" / "key")


def by_name(checks):
    return {c.name: c for c in checks}


# doctor ----------------------------------------------------------------------------

def test_doctor_on_a_hardened_wsl2_machine_is_ready(tmp_path):
    m = machine(tmp_path)
    checks = by_name(doctor.run(m))
    assert {n: c.status for n, c in checks.items()} == {
        "platform": "ok", "sandbox": "ok", "claude login": "ok", "windows": "ok",
        "approval key": "ok", "signing key": "info"}
    assert checks["platform"].detail == "linux on wsl2"
    assert checks["sandbox"].detail == "bubblewrap, socat, srt"
    assert checks["windows"].detail == "interop off, path off, drives off"
    assert checks["signing key"].detail == "none: accept commits won't be signed"


def test_doctor_creates_the_approval_key_private_and_keeps_it(tmp_path):
    m = machine(tmp_path)
    first = doctor.check_approval_key(m)
    assert first.status == "ok" and first.detail.startswith("created ")
    key = m.key.read_text()
    assert len(key.strip()) == 64
    assert stat.S_IMODE(m.key.stat().st_mode) == 0o600
    assert stat.S_IMODE(m.key.parent.stat().st_mode) == 0o700
    again = doctor.check_approval_key(m)
    assert again.status == "ok" and not again.detail.startswith("created")
    assert m.key.read_text() == key  # never replaced


def test_a_key_others_can_read_fails(tmp_path):
    m = machine(tmp_path)
    doctor.check_approval_key(m)
    os.chmod(m.key, 0o644)
    c = doctor.check_approval_key(m)
    assert c.status == "fail" and "chmod 600" in c.hint


@pytest.mark.parametrize("missing", ["bwrap", "socat", "srt"])
def test_a_missing_sandbox_tool_is_a_failure(tmp_path, missing):
    tools = {"bwrap", "socat", "srt", "claude"} - {missing}
    c = doctor.check_sandbox(machine(tmp_path, tools=tools))
    assert c.status == "fail" and c.detail.startswith("missing ")


@pytest.mark.parametrize("setup,detail", [
    ({"files": ("/proc/sys/fs/binfmt_misc/WSLInterop",)}, "interop on, path off, drives off"),
    ({"path": "/usr/bin:/mnt/c/Windows/system32"}, "interop off, path on, drives off"),
    ({"mounts": HARDENED_MOUNTS + "C:\\134 /mnt/c 9p rw,aname=drvfs 0 0\n"}, "interop off, path off, drives on"),
])
def test_windows_reach_is_a_warning_with_the_hardening_link(tmp_path, setup, detail):
    c = doctor.check_windows(machine(tmp_path, **setup))
    assert (c.status, c.detail) == ("warn", detail)
    assert doctor.HARDENING in c.hint


def test_wrong_platforms_fail_and_plain_linux_is_fine(tmp_path):
    assert doctor.check_platform(machine(tmp_path, platform="win32")).status == "fail"
    assert doctor.check_platform(machine(tmp_path, osrelease="4.4.0-19041-Microsoft")).status == "fail"  # wsl1
    plain = machine(tmp_path, osrelease="6.8.0-generic")
    assert doctor.check_platform(plain).detail == "linux"
    assert doctor.check_windows(plain).detail == "not wsl"


def test_no_claude_login_fails(tmp_path):
    assert doctor.check_login(machine(tmp_path, logged_in=False)).status == "fail"
    assert doctor.check_login(machine(tmp_path, tools=("bwrap", "socat", "srt"))).detail == "claude not found"


def test_doctor_report_lines_up_and_the_cli_exit_code_follows_failures(tmp_path, monkeypatch, capsys):
    lines = doctor.report(doctor.run(machine(tmp_path, signing="~/.ssh/id_ed25519.pub")))
    assert lines[0].startswith("platform      linux on wsl2") and lines[0].endswith("ok")
    assert len({line.rindex(" ok") for line in lines if line.endswith(" ok")}) == 1  # one status column

    broken = machine(tmp_path, tools=("claude",))
    interop_on = machine(tmp_path, files=("/proc/sys/fs/binfmt_misc/WSLInterop",))
    monkeypatch.setattr(doctor, "Machine", lambda: broken)
    assert main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "fail" in out and out.endswith("not ready: fix what failed above.\n")
    monkeypatch.setattr(doctor, "Machine", lambda: interop_on)
    assert main(["doctor"]) == 0  # a warning isn't a failure
    assert capsys.readouterr().out.endswith("ready.\n")


# worktrees ----------------------------------------------------------------------------

def test_worktrees_live_outside_the_repo(repo, private_home):
    proj = Project.init(repo)
    wt = Path(proj.new_task("x")["worktree"])
    assert wt.is_relative_to(private_home / "data" / "parallax" / "worktrees")
    assert not wt.is_relative_to(repo)
    assert STATE_DIR not in wt.parts
    assert (repo / STATE_DIR / ".gitignore").read_text() == "*.lock\n"


def test_two_repos_with_one_name_get_separate_worktree_homes(tmp_path):
    from parallax.core import worktrees_home
    assert worktrees_home(tmp_path / "a" / "app") != worktrees_home(tmp_path / "b" / "app")


# the cuts ---------------------------------------------------------------------------------

def test_init_writes_no_mission_and_profiles_are_refused(repo):
    Project.init(repo)
    assert not (repo / "mission.md").exists()
    (repo / POLICY_FILE).write_text('[actions]\n"fs.read" = "allow"\n[profiles.docs.actions]\n"fs.read" = "allow"\n')
    with pytest.raises(ParallaxError, match="profiles are gone"):
        Project(repo)


@pytest.mark.parametrize("cmd", ["goal", "pulse", "evidence", "check", "review"])
def test_cut_commands_are_gone(cmd, capsys):
    with pytest.raises(SystemExit):
        main([cmd, "x"])


# housekeeping ---------------------------------------------------------------------------

def test_a_quiet_run_is_flagged_stuck_once(repo):
    proj = Project.init(repo)
    tid = proj.new_task("x")["task"]
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    assert flag_stale_runs(proj) == []  # still fresh
    later = datetime.now(timezone.utc) + timedelta(minutes=61)
    assert flag_stale_runs(proj, later) == [tid]
    assert proj.task(tid)["status"] == "stuck"
    assert flag_stale_runs(proj, later) == []  # already flagged
    [item] = proj.inbox()
    assert item["kind"] == "stuck.raised" and "no activity for 60 minutes" in item["reason"]
