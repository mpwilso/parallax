"""Field's browser calls through Polarizer: off unless the policy turns it on, and when on, Polarizer
runs outside the sandbox in front of the same sandboxed server, and every stop it causes comes to you.
No Polarizer install is needed here; tests/polarizer_fake.py writes what serve would write. The one
test with the real program runs only with the pinned release."""
import importlib.metadata
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

from parallax import fieldproxy, memcap, uitest
from parallax.core import POLICY_FILE, Project
from parallax.policy import DEFAULT_UI_TESTER, Policy
from polarizer_fake import Ledger, call, config_of, session
from test_ui_tester import UI_POLICY, FakeTester, flow_runner, kinds, pilot, proj  # noqa: F401
from test_lifecycle_gates import make_key
from parallax import build

ROUTED = UI_POLICY + "polarizer = true\n"
REAL_PROGRAM = fieldproxy.program


@pytest.fixture
def routed(repo, monkeypatch, tmp_path):
    (repo / POLICY_FILE).write_text(ROUTED)
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    tools = tmp_path / "tools"
    monkeypatch.setattr(uitest, "ensure_tools", lambda: uitest.Tools(tools, tools / "chrome"))
    monkeypatch.setattr(fieldproxy, "program", lambda: "/opt/polarizer/bin/polarizer")
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)]]))
    return Project.init(repo)


def approved() -> Ledger:
    """A Field ledger where you've approved the browser tools once before."""
    ledger = Ledger(fieldproxy.ledger_dir())
    ledger.add("tool.approved", upstream="playwright", tool="browser_navigate", def_hash="a" * 64, actor="you", group=None)
    ledger.head()
    return ledger


class Behind(FakeTester):
    """Field, with Polarizer in front: what serve records happens around the same run."""

    def __init__(self, record, **kw):
        super().__init__(**kw)
        self.record = record

    def run(self, goal, cwd, server, allowed):
        out = super().run(goal, cwd, server, allowed)
        self.record(Ledger(fieldproxy.ledger_dir()), server)
        return out


def calls_made(ledger, server):
    s = session(ledger, server)
    call(ledger, s)
    call(ledger, s, "browser_take_screenshot")


# the setting ---------------------------------------------------------------------------------------------

def test_off_unless_set_and_absent_means_the_settings_are_as_before():
    assert Policy({}).ui_tester == DEFAULT_UI_TESTER and "polarizer" not in Policy({}).ui_tester
    assert Policy({}, ui_tester={"polarizer": True}).ui_tester["polarizer"] is True
    with pytest.raises(ValueError, match="polarizer must be true or false"):
        Policy({}, ui_tester={"polarizer": "/somewhere/a/repo/picked"})
    assert not fieldproxy.on(Policy({}).ui_tester)


def test_off_field_starts_the_server_itself_and_polarizer_never_runs(proj, monkeypatch):  # noqa: F811
    monkeypatch.setattr(fieldproxy, "program", lambda: pytest.fail("polarizer was looked for with the setting off"))
    tester = FakeTester()
    monkeypatch.setattr(uitest, "TESTER", tester)
    monkeypatch.setattr(uitest, "FLOW_RUNNER", flow_runner([[("opens", True)]]))
    tid, status = pilot(proj, tester, None)
    assert status == "ready"
    assert tester.calls[0]["server"]["args"][:4] == ["-I", str(Path(memcap.__file__).resolve()), str(memcap.COMMAND), "--"]
    assert not kinds(proj, "uitest.polarizer") and not fieldproxy.ledger_dir().exists()
    assert not list(fieldproxy.ledger_dir().parent.rglob("polarizer.toml"))


# on ----------------------------------------------------------------------------------------------------------

def test_on_polarizer_fronts_the_same_sandboxed_server_and_the_run_is_recorded(routed, monkeypatch):
    ledger = approved()
    tester = Behind(calls_made)
    monkeypatch.setattr(uitest, "TESTER", tester)
    tid, status = pilot(routed, tester, None)
    assert status == "ready"
    srv = tester.calls[0]["server"]
    assert srv["command"] == "/opt/polarizer/bin/polarizer" and srv["args"][:2] == ["serve", "--config"]
    assert "--no-holds" not in srv["args"] and srv["env"] == {"HOME": str(Path.home())}
    cfg = tomllib.loads(config_of(srv).read_text())
    assert Path(cfg["ledger_dir"]) == fieldproxy.ledger_dir().resolve()
    up = cfg["upstream"]["playwright"]
    # the upstream is exactly what Field ran without Polarizer: the app and browser in srt, under the cap
    assert up["command"] == sys.executable and up["args"][4] == "srt" and up["env"]["HOME"].endswith("work")
    assert "--allowed-origins http://127.0.0.1:8765" in up["args"][-1]
    assert up["tools"] == {t: {"class": "open-world"} for t in uitest.BROWSER_TOOLS}  # nothing Field may use is held
    assert "policy" not in cfg and up["connect_timeout_seconds"] == 20
    allowed = tester.calls[0]["allowed"]
    assert allowed("mcp__playwright__playwright__browser_click", {})
    assert not allowed("mcp__playwright__browser_click", {}) and not allowed("mcp__playwright__playwright__browser_install", {})
    rec = kinds(routed, "uitest.polarizer", tid)[-1]
    assert rec["reason"] == "" and rec["data"]["calls"] == 2 and rec["data"]["holds"] == 0
    assert rec["data"]["chain_id"] == ledger.entries()[0]["data"]["chain_id"] and rec["data"]["polarizer_version"] == "0.6.0"
    assert rec["data"]["ledger_dir"] == str(fieldproxy.ledger_dir().resolve()) and rec["data"]["sessions"] == ["8c1d4f7a2b5e9036"]
    assert rec["data"]["last_seq"] == ledger.entries()[-1]["seq"]  # the session's calls, findable later
    assert kinds(routed, "uitest.recorded", tid)
    from parallax.accept import accept
    record = (Path(accept(routed, tid)["data"]["docs_moved_to"]) / "record.md").read_text()
    assert (f"Field's browser calls: 2 through Polarizer, recorded in its ledger at {fieldproxy.ledger_dir().resolve()} "
            f"(chain {rec['data']['chain_id']}, seq {rec['data']['first_seq']} to {rec['data']['last_seq']}; ledger {rec['id']})") in record


def test_before_anything_is_approved_the_task_stops_for_you_before_field_spends(routed, monkeypatch):
    listed = []

    def lister(route):  # serve with its input closed: it lists the tools and records what no decision covers
        listed.append(route)
        ledger = Ledger(route.ledger)
        s = session(ledger, route.server)
        for tool in sorted(uitest.BROWSER_TOOLS):
            ledger.add("tool.seen", session=s, upstream="playwright", tool=tool, def_hash="b" * 64)
    monkeypatch.setattr(fieldproxy, "LISTER", lister)
    tester = FakeTester()
    monkeypatch.setattr(uitest, "TESTER", tester)
    tid, status = pilot(routed, tester, None)
    assert status == "disputed" and listed and not tester.calls
    why = kinds(routed, "disagreement.raised", tid)[-1]["reason"]
    assert "Polarizer is keeping 19 of Field's browser tools back until you approve them" in why
    assert f"polarizer pending --ledger-dir {fieldproxy.ledger_dir().resolve()}" in why
    assert not kinds(routed, "uitest.started", tid)


def test_where_each_tool_stands_follows_its_latest_decision(tmp_path):
    ledger = Ledger(tmp_path / "pz")
    r = fieldproxy.Route({}, tmp_path / "polarizer.toml", "0" * 64, tmp_path / "pz", "run1", frozenset({"browser_click", "browser_type"}))
    assert fieldproxy.needs_listing(r)  # nothing approved yet
    for tool in ("browser_click", "browser_type", "browser_run_code"):
        ledger.add("tool.seen", session="s1", upstream="playwright", tool=tool, def_hash="b" * 64)
    assert fieldproxy.decisions(ledger.entries(), r.tools) == {"browser_click": "new", "browser_type": "new"}
    for tool in ("browser_click", "browser_type"):
        ledger.add("tool.approved", upstream="playwright", tool=tool, def_hash="b" * 64, actor="you", group="g1")
    assert not fieldproxy.needs_listing(r)  # browser_run_code, which Field never gets, waits without blocking
    ledger.add("tool.drift", session="s2", upstream="playwright", tool="browser_type", approved_hash="b" * 64, live_hash="c" * 64)
    assert fieldproxy.decisions(ledger.entries(), r.tools)["browser_type"] == "changed" and fieldproxy.needs_listing(r)


def test_a_listing_pass_whose_server_never_connected_says_so(routed, monkeypatch):
    def lister(route):
        ledger = Ledger(route.ledger)
        session(ledger, route.server, connected=False)
        ledger.add("upstream.connected", prefix="playwright", error="timeout after 20 s")
    monkeypatch.setattr(fieldproxy, "LISTER", lister)
    monkeypatch.setattr(uitest, "TESTER", lambda *a: pytest.fail("Field ran though its tools were never listed"))
    tid, status = pilot(routed, None, None)
    assert status == "disputed"
    assert "Polarizer couldn't list Field's browser tools: its browser server didn't connect (timeout after 20 s)" in         kinds(routed, "disagreement.raised", tid)[-1]["reason"]


def test_a_held_call_is_a_stop_for_you_never_a_retry(routed, monkeypatch):
    approved()

    def held(ledger, server):
        s = session(ledger, server)
        ledger.add("hold.created", session=s, hold="1" * 16, tool="playwright__browser_evaluate", args_commit="4" * 64,
                   **{"class": None}, class_from=None, rule="unclassified", reason="no class", timeout_seconds=300)
        ledger.add("hold.expired", session=s, hold="1" * 16, reason="timeout")
        ledger.add("call.refused", session=s, tool="playwright__browser_evaluate", reason="held, then not allowed", hold="1" * 16)
    tester = Behind(held)
    monkeypatch.setattr(uitest, "TESTER", tester)
    tid, status = pilot(routed, tester, None)
    assert status == "disputed" and len(tester.calls) == 1
    why = kinds(routed, "disagreement.raised", tid)[-1]["reason"]
    assert "Polarizer held 1 of Field's browser calls for a person (playwright__browser_evaluate)" in why
    assert "nothing was retried" in why and not kinds(routed, "uitest.recorded", tid)


def test_a_polarizer_ledger_that_doesnt_verify_stops_the_task(routed, monkeypatch):
    approved()

    def tamper(ledger, server):
        calls_made(ledger, server)
        lines = ledger.path.read_bytes().splitlines(True)
        ledger.path.write_bytes(b"".join(lines[:-2] + [lines[-1]]))
    monkeypatch.setattr(uitest, "TESTER", Behind(tamper))
    tid, status = pilot(routed, None, None)
    assert status == "disputed"
    assert "Polarizer's ledger for Field doesn't verify: tampered: line" in kinds(routed, "disagreement.raised", tid)[-1]["reason"]


def test_a_run_polarizer_never_recorded_stops_the_task(routed, monkeypatch):
    approved()
    monkeypatch.setattr(uitest, "TESTER", Behind(lambda ledger, server: None))
    tid, status = pilot(routed, None, None)
    assert status == "disputed"
    assert "Polarizer didn't start Field's session" in kinds(routed, "disagreement.raised", tid)[-1]["reason"]


def test_without_polarizer_on_path_field_doesnt_run_and_you_hear_why(routed, monkeypatch):
    monkeypatch.setattr(fieldproxy, "program", REAL_PROGRAM)  # the real lookup, on a PATH with no polarizer
    which = fieldproxy.shutil.which
    monkeypatch.setattr(fieldproxy.shutil, "which", lambda name, *a, **k: None if name == "polarizer" else which(name, *a, **k))
    monkeypatch.setattr(uitest, "TESTER", lambda *a: pytest.fail("Field ran without its Polarizer"))
    tid, status = pilot(routed, None, None)
    assert status == "disputed"
    assert "polarizer isn't on PATH" in kinds(routed, "disagreement.raised", tid)[-1]["reason"]


def test_the_ledger_is_never_inside_parallaxs_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(Path.home() / ".local" / "share" / "parallax" / "x"))
    with pytest.raises(fieldproxy.RouteError, match="where Polarizer must not write"):
        fieldproxy.check_dir(fieldproxy.ledger_dir())
    for bad in fieldproxy.forbidden():
        with pytest.raises(fieldproxy.RouteError):
            fieldproxy.check_dir(bad / "polarizer-field")
    assert fieldproxy.check_dir(tmp_path / "polarizer-field") == (tmp_path / "polarizer-field").resolve()


def test_the_config_reads_back_exactly_whatever_the_values_hold(tmp_path):
    server = {"command": "/a b/python", "args": ['say "hi"\\ there', "cafÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Â ÃƒÂ¢Ã¢â€šÂ¬Ã¢â€žÂ¢ÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â© \U0001f600", "\x7f\t\n"],
              "env": {"HOME": "/h", "WEIRD KEY": "x=y"}}
    text = fieldproxy.config_text(server, tmp_path, {"browser_click"}, "run1")
    up = tomllib.loads(text)["upstream"]["playwright"]
    assert (up["command"], up["args"], up["env"]) == (server["command"], server["args"], server["env"])


# the real program ------------------------------------------------------------------------------------------

def _pinned() -> Path | None:
    """The polarizer that scripts/test.sh installs beside this interpreter, at the pinned version."""
    try:
        if importlib.metadata.version("polarizer") != fieldproxy.VERSION:
            return None
    except importlib.metadata.PackageNotFoundError:
        return None
    exe = Path(sys.executable).parent / "polarizer"
    return exe if exe.exists() else None


def test_the_pin_is_the_same_in_the_code_and_in_scripts_test_sh():
    text = (Path(__file__).resolve().parent.parent / "scripts" / "test.sh").read_text()
    assert f'POLARIZER="{fieldproxy.WHEEL}"' in text and '--with "$POLARIZER"' in text


@pytest.mark.skipif(_pinned() is None, reason=f"needs polarizer {fieldproxy.VERSION}, which scripts/test.sh installs")
def test_the_pinned_polarizer_accepts_the_config_and_refuses_a_ledger_in_parallaxs_folder(tmp_path):
    exe = _pinned()
    server = {"command": sys.executable, "args": ["-c", "pass"], "env": {"HOME": str(tmp_path / "work")}}
    cfg = tmp_path / "polarizer.toml"
    cfg.write_text(fieldproxy.config_text(server, tmp_path / "pz", uitest.BROWSER_TOOLS, "run1"))
    home = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
    out = subprocess.run([str(exe), "verify", "--config", str(cfg)], capture_output=True, text=True, env=home, timeout=60)
    # its parser took every key: the only complaint is that no ledger exists yet
    assert (out.returncode, out.stdout.strip()) == (2, f"no ledger at {tmp_path / 'pz'}"), out.stderr
    inside = tmp_path / ".local" / "share" / "parallax" / "pz"
    cfg.write_text(fieldproxy.config_text(server, inside, uitest.BROWSER_TOOLS, "run2"))
    out = subprocess.run([str(exe), "serve", "--config", str(cfg)], capture_output=True, text=True, env=home,
                         stdin=subprocess.DEVNULL, timeout=60)
    assert out.returncode == 2 and "which Polarizer must not write to" in out.stderr and not inside.exists()
