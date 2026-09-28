import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from fakes import FakeChecker, ScriptedAgent
from parallax import rules
from parallax.cli import main
from parallax.core import POLICY_FILE, Project
from parallax.evidence import raise_promotions, table
from parallax.inbox import resolve_item
from parallax.policy import Policy, normalize_detail
from parallax.pulse import pulse
from parallax.rules import RulesEditError
from parallax.runner import run_task

POLICY = """\
# my policy
[actions]
"fs.read"   = "allow"
"fs.write"  = "ask"
"shell.run" = "ask"

[limits]
max_parallel = 4
"""


def setup(repo: Path, policy: str = POLICY) -> Project:
    (repo / POLICY_FILE).write_text(policy)
    return Project.init(repo)


def approve_runs(proj: Project, n: int, command: str = "pytest -q", outcome: bool = True, tasks=None) -> list[str]:
    """n human rulings on the same command, spread across tasks with different worktrees."""
    tasks = tasks or [proj.new_task(f"t{i}")["task"] for i in range(3)]
    for i in range(n):
        t = proj.task(tasks[i % len(tasks)])
        req = proj.check(t["task"], "shell.run", f'cd "{t["worktree"]}" && {command}')["entry"]
        proj.resolve(req["id"], outcome, "fine" if outcome else "no")
    return tasks


KEY = 'cd "<worktree>" && pytest -q'


# exact rules -----------------------------------------------------------------------

def test_exact_rules_beat_action_level_and_nothing_else():
    p = Policy({"shell.run": "ask"}, exact={"default": {"shell.run": {"pytest -q": "allow"}}})
    assert p.ruling("shell.run", key="pytest -q") == "allow"
    assert p.ruling("shell.run", key="pytest -q -x") == "ask"  # exact means exact
    assert p.ruling("fs.write", key="pytest -q") == "deny"
    for bad in ({"readonly": {"fs.write": {"a": "allow"}}}, {"default": {"git.merge": {"x": "allow"}}},
                {"nope": {"fs.read": {"a": "allow"}}}, {"default": {"shell.run": {"a": "yes"}}}):
        with pytest.raises(ValueError):
            Policy({}, exact=bad)


def test_details_normalize_across_worktrees(tmp_path):
    wt = tmp_path / ".parallax" / "worktrees" / "abc123"
    assert normalize_detail("fs.write", str(wt / "src" / "calc.py"), wt) == "src/calc.py"
    assert normalize_detail("fs.write", str(tmp_path / "elsewhere.py"), wt) == str(tmp_path / "elsewhere.py")
    assert normalize_detail("shell.run", f'cd "{wt.as_posix()}" && pytest -q', wt) == KEY
    assert normalize_detail("shell.run", f'cd "{wt}" && pytest -q', wt) == KEY
    assert normalize_detail("shell.run", "  pytest -q ", None) == "pytest -q"


def test_check_matches_exact_rules(repo):
    proj = setup(repo, POLICY + '[exact."shell.run"]\n"pytest -q" = "allow"\n[exact."fs.write"]\n"calc.py" = "allow"\n')
    t = proj.new_task("x")
    assert proj.check(t["task"], "shell.run", "pytest -q")["ruling"] == "allow"
    assert proj.check(t["task"], "shell.run", "pytest -q; rm -rf .")["ruling"] == "ask"
    res = proj.check(t["task"], "fs.write", str(Path(t["worktree"]) / "calc.py"))
    assert res["ruling"] == "allow" and res["entry"]["data"]["key"] == "calc.py"


# promotions ------------------------------------------------------------------------

def test_promotion_needs_ten_clean_approvals(repo):
    proj = setup(repo)
    tasks = approve_runs(proj, 9)
    assert raise_promotions(proj) == []
    approve_runs(proj, 1, tasks=tasks)
    [p] = raise_promotions(proj)
    assert p["kind"] == "promotion.raised" and p["actor"] == "parallax"
    assert (p["data"]["action"], p["data"]["key"], p["data"]["approvals"]) == ("shell.run", KEY, 10)
    assert len(p["data"]["evidence"]) == 10
    assert raise_promotions(proj) == []  # already pending


def test_any_rejection_or_old_evidence_blocks_promotion(repo):
    proj = setup(repo)
    tasks = approve_runs(proj, 10)
    approve_runs(proj, 1, outcome=False, tasks=tasks)
    assert raise_promotions(proj) == []

    approve_runs(proj, 10, command="ruff check .", tasks=tasks)  # clean record, but check the window
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert raise_promotions(proj, now=later) == []
    assert len(raise_promotions(proj)) == 1


def test_non_string_ruling_is_a_clean_error(repo):
    (repo / POLICY_FILE).write_text('[actions]\n"shell.run" = { a = 1 }\n')
    with pytest.raises(Exception, match="bad policy"):
        Project.init(repo)


def test_rejected_promotion_needs_fresh_approvals(repo):
    proj = setup(repo)
    tasks = approve_runs(proj, 10)
    [p] = raise_promotions(proj)
    resolve_item(proj, p["id"], False, "not yet")
    approve_runs(proj, 9, tasks=tasks)
    assert raise_promotions(proj) == []
    approve_runs(proj, 1, tasks=tasks)
    assert len(raise_promotions(proj)) == 1


def test_approving_a_promotion_writes_the_policy(repo):
    proj = setup(repo)
    tasks = approve_runs(proj, 10)
    before = tomllib.loads((repo / POLICY_FILE).read_text())
    [p] = raise_promotions(proj)
    out = resolve_item(proj, p["id"], True, "tests are safe")
    assert out.changed.startswith("policy updated: exact shell.run")

    text = (repo / POLICY_FILE).read_text()
    assert text.startswith("# my policy") and f"# ledger {p['id']}" in text
    after = tomllib.loads(text)
    assert after == {**before, "exact": {"shell.run": {KEY: "allow"}}}
    [change] = [e for e in proj.ledger.entries() if e["kind"] == "rules.changed"]
    assert change["data"]["item"] == p["id"]

    t = proj.task(tasks[0])
    assert proj.check(t["task"], "shell.run", f'cd "{t["worktree"]}" && pytest -q')["ruling"] == "allow"
    [(k, s, status)] = [r for r in table(proj) if r[0][2] == KEY]
    assert status == "allowed"


def test_an_unverifiable_edit_writes_and_resolves_nothing(repo):
    policy = 'exact = { "shell.run" = { "ls" = "deny" } }\n[actions]\n"shell.run" = "ask"\n'  # inline: can't extend
    proj = setup(repo, policy)
    approve_runs(proj, 10)
    [p] = raise_promotions(proj)
    with pytest.raises(RulesEditError) as err:
        resolve_item(proj, p["id"], True, "yes")
    assert KEY.replace('"', '\\"') in err.value.snippet
    assert (repo / POLICY_FILE).read_text() == policy
    assert p["id"] in [e["id"] for e in proj.inbox()]


def test_pulse_raises_promotions(repo):
    proj = setup(repo)
    approve_runs(proj, 10)
    res = pulse(proj, launch=lambda project, tid: 0)
    assert any("promotion proposed" in f for f in res["findings"])
    assert [e["kind"] for e in proj.inbox()] == ["promotion.raised"]


# the recorded-change trail -----------------------------------------------------------

def test_a_recorded_change_mid_run_keeps_the_maker_going(repo):
    proj = setup(repo, POLICY.replace('"shell.run" = "ask"', '"shell.run" = "deny"'))
    tid = proj.new_task("x")["task"]
    promote = lambda cwd: rules.set_policy_rule(proj, "default", "shell.run", "git status", "allow", "manual")
    agent = ScriptedAgent(steps=[("shell", ["git", "status"]), ("call", promote), ("shell", ["git", "status"])])
    assert run_task(proj, tid, agent, FakeChecker()) == "ready"
    assert [(p.allowed, p.stop) for _, _, p in agent.results] == [(False, False), (True, False)]


# cli -------------------------------------------------------------------------------------

def test_cli_shows_promotions_and_evidence(repo, monkeypatch, capsys):
    proj = setup(repo)
    approve_runs(proj, 10)
    [p] = raise_promotions(proj)
    monkeypatch.chdir(repo)

    main(["evidence"])
    out = capsys.readouterr().out
    assert "promotion pending" in out and "10" in out

    main(["inbox"])
    out = capsys.readouterr().out
    assert "promotions and laws" in out and f"promote (default) shell.run: {KEY}" in out
    assert "evidence: 10 approvals, 0 rejections" in out

    assert main(["approve", p["id"], "--reason", "safe"]) == 0
    assert "policy updated: exact shell.run" in capsys.readouterr().out
    assert proj.ledger.verify()[0]
