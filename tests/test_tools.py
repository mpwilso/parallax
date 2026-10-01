"""Missing programs are caught before any task runs (real use, 2026-10-01: "uv not found" mid-task)."""
import os
import stat
from pathlib import Path

import pytest

from fakes import FakeDrafter
from parallax import build, decide, doctor, pilot, show, tools, views
from parallax.core import POLICY_FILE, Project
from test_lifecycle_gates import WANT, docs, make_key
from test_no_dead_ends import pilot_once

SETUP = 'uv venv -q "$PARALLAX_VENV"'
UV_MESSAGE = ('uv not found: the [build] setup command needs it. fix: install it with curl -LsSf https://astral.sh/uv/install.sh | sh. '
              'if it\'s installed already, add its folder to PATH in ~/.bashrc, for example export PATH="$HOME/.local/bin:$PATH", '
              'then restart parallax ui from a new terminal.')


@pytest.fixture
def bare_shell(tmp_path, monkeypatch):
    """A shell started without uv on its PATH: the system's programs but uv, and an empty home."""
    bin_dir, home = tmp_path / "bin", tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    for folder in ("/usr/local/bin", "/usr/bin", "/bin"):
        for tool in (Path(folder).iterdir() if Path(folder).is_dir() else []):
            if tool.name not in ("uv", "uvx") and not (bin_dir / tool.name).exists():
                (bin_dir / tool.name).symlink_to(tool)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(home))
    return home


def fake_uv(home: Path) -> Path:
    """uv where its installer puts it: ~/.local/bin. This one only makes the venv folder."""
    uv = home / ".local" / "bin" / "uv"
    uv.parent.mkdir(parents=True)
    uv.write_text('#!/bin/sh\nfor a; do last="$a"; done\nmkdir -p "$last/bin"\n')
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    return uv


def setup_project(repo, monkeypatch):
    (repo / POLICY_FILE).write_text(f"[build]\nsetup = '{SETUP}'\n")
    make_key()
    proj = Project.init(repo)
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    return proj, pilot.intake(proj, WANT)["task"]


def test_the_path_adds_the_installers_folder_after_yours_never_before(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    local = str(home / ".local" / "bin")
    assert tools.path({"PATH": "/usr/bin:/bin"}) == f"/usr/bin:/bin:{local}"
    assert tools.path({"PATH": f"{local}:/usr/bin"}) == f"{local}:/usr/bin"  # already there: kept where you put it
    assert tools.path({"PATH": ""}) == local


def test_the_programs_a_command_runs_are_read_from_it():
    assert tools.commands(SETUP + " && uv pip install -q pytest") == ["uv"]
    assert tools.commands("PYTHONPATH=. python3 tests/ui_app.py 8765") == ["python3"]
    assert tools.commands("scripts/test.sh") == []  # a path: it can't be read further
    assert tools.commands("export X=1; cd src && make -j2 2>&1 | tee log > out.txt") == ["make", "tee"]
    assert tools.commands("python3 -c 'import time; time.sleep(0.2)'") == ["python3"]  # a ; inside quotes runs nothing
    assert tools.commands("uv venv\nnpm ci") == ["uv", "npm"]
    assert tools.commands("echo no >&2; exit 3") == []  # builtins run no program
    from parallax.policy import Policy
    policy = Policy(build={"setup": SETUP}, ui_tester={"enabled": True, "start": "npm run dev", "url": "http://127.0.0.1:5173/"})
    want = dict(tools.needed(policy, "linux"))
    assert want["uv"] == "the [build] setup command" and want["npm"] == "the [ui_tester] start command"
    assert {"git", "claude", "srt", "bwrap", "socat"} <= set(want)


@pytest.mark.parametrize("output,tool", [("sh: 1: uv: not found", "uv"), ("bash: line 1: uvx: command not found", "uvx"),
                                         ("zsh: command not found: uv", "uv"), ("error: no such file", None)])
def test_a_shells_not_found_names_the_program(output, tool):
    found = tools.not_found(output, "the [build] setup command")
    assert (found.tool if found else None) == tool


def test_setup_finds_uv_in_local_bin_when_the_shell_has_no_uv(repo, monkeypatch, bare_shell):
    fake_uv(bare_shell)
    proj, tid = setup_project(repo, monkeypatch)
    assert pilot_once(proj, tid, FakeDrafter(docs())) == "ready", [e["reason"] for e in proj.ledger.entries() if e["kind"] == "stuck.raised"]
    [ran] = [e for e in proj.ledger.entries() if e["kind"] == "setup.ran"]
    assert ran["data"]["exit"] == 0


def test_a_missing_uv_stops_before_setup_runs_and_the_card_names_it_and_the_fix(repo, monkeypatch, bare_shell):
    proj, tid = setup_project(repo, monkeypatch)
    assert pilot_once(proj, tid, FakeDrafter(docs())) == "stuck"
    assert not [e for e in proj.ledger.entries() if e["kind"] == "setup.ran"]  # nothing ran half way
    [item] = proj.inbox()
    assert item["reason"] == "uv not found: the [build] setup command needs it" and item["data"]["missing_tool"] == "uv"
    dec = decide.decision(proj, tid)
    assert dec.kind == "tool" and dec.recommend == "retry" and [o.name for o in dec.options] == ["retry", "drop"]
    card = show.report(proj, tid)
    assert "Bottom line: Needs you: uv not found." in card
    assert "Fix: install it with curl -LsSf https://astral.sh/uv/install.sh | sh." in card and "restart parallax ui" in card
    assert "Traceback" not in card and "ParallaxError" not in card
    assert views.card(proj, tid)["actions"]["kind"] == "decide"


def test_a_program_setup_runs_inside_a_script_is_named_too(repo, monkeypatch, bare_shell):
    (repo / "make-venv.sh").write_text("#!/bin/sh\nnotathing --venv \"$PARALLAX_VENV\"\n")
    (repo / "make-venv.sh").chmod(0o755)
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "make-venv.sh"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "script"], check=True)
    (repo / POLICY_FILE).write_text("[build]\nsetup = './make-venv.sh'\n")
    make_key()
    proj = Project.init(repo)
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_once(proj, tid, FakeDrafter(docs())) == "stuck"
    [item] = proj.inbox()
    assert item["data"]["missing_tool"] == "notathing" and "install notathing" in item["data"]["fix"]


def test_the_uv_message_is_exactly_this():
    assert tools.Missing("uv", "the [build] setup command").message == UV_MESSAGE


# doctor ----------------------------------------------------------------------------------------------------

def test_doctor_names_a_missing_uv_and_its_fix(tmp_path):
    from test_doctor import machine
    m = machine(tmp_path, tools=("bwrap", "socat", "srt", "claude", "git"))
    m.needed = tools.needed(None, "linux")
    c = doctor.check_tools(m)
    assert c.status == "fail" and c.detail == "missing uv"
    assert c.hint.startswith("install it with curl -LsSf https://astral.sh/uv/install.sh | sh")
    assert any(line.startswith("build tools") and "fail: install it with" in line for line in doctor.report([c]))


def test_doctor_says_when_it_finds_uv_off_your_path(tmp_path):
    from test_doctor import machine
    m = machine(tmp_path, tools=("bwrap", "socat", "srt", "claude", "git"))
    m.needed = tools.needed(None, "linux")
    m.which = lambda b: str(Path.home() / ".local/bin/uv") if b == "uv" else f"/usr/bin/{b}"
    c = doctor.check_tools(m)
    assert c.status == "ok" and c.detail == "git, uv (uv at ~/.local/bin/uv, not on your PATH; parallax finds it there)"


# parallax ui -----------------------------------------------------------------------------------------------

def test_parallax_ui_prints_what_is_missing_at_startup_and_the_page_shows_it(repo, monkeypatch, capsys):
    from parallax import ui
    from parallax.cli import main
    (repo / POLICY_FILE).write_text(f"[build]\nsetup = '{SETUP}'\n")
    make_key()
    Project.init(repo)
    started = {}

    class Fake(ui.UI):
        def __init__(self, root, port=None, new_token=False):
            super().__init__(root, 0, new_token, find=lambda t: None if t == "uv" else f"/usr/bin/{t}")
            started["app"] = self

        def serve(self, open_browser=True):
            self.server.server_close()  # never served, so nothing to shut down

    monkeypatch.setattr(ui, "UI", Fake)
    monkeypatch.chdir(repo)
    assert main(["ui", "--no-open"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert UV_MESSAGE in out
    assert started["app"].missing() == [UV_MESSAGE]


def test_nothing_missing_means_no_note(repo):
    from parallax import ui
    make_key()
    proj = Project.init(repo)
    app = ui.UI(proj.root, port=0, find=lambda t: f"/usr/bin/{t}")
    try:
        assert app.missing() == []
    finally:
        app.server.server_close()


def test_a_real_lookup_uses_the_resolved_path(tmp_path, monkeypatch, bare_shell):
    assert tools.which("uv") is None
    fake_uv(bare_shell)
    assert tools.which("uv") == str(bare_shell / ".local" / "bin" / "uv")
    assert os.environ["PATH"].endswith("/bin")  # yours is never changed
