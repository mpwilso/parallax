import json
import subprocess
from pathlib import Path

import pytest

from fakes import FakeChecker, ScriptedAgent
from parallax import evals
from parallax.cli import main
from parallax.core import Project

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
HIDDEN = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
OLD = "def test_old():\n    assert True\n"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A local 'upstream' with a bug and its merged fix, plus a parallax checkout with a cases file."""
    monkeypatch.setattr(evals, "home", lambda: tmp_path / "home")
    up = tmp_path / "upstream"
    (up / "tests").mkdir(parents=True)
    git(up, "init", "-q")
    (up / "calc.py").write_text(BUGGY)
    (up / "tests" / "test_old.py").write_text(OLD)
    git(up, "add", ".")
    git(up, "commit", "-q", "-m", "base")
    base = git(up, "rev-parse", "HEAD")
    (up / "calc.py").write_text(FIXED)
    (up / "tests" / "test_calc.py").write_text(HIDDEN)
    git(up, "add", ".")
    git(up, "commit", "-q", "-m", "fix add")
    fix = git(up, "rev-parse", "HEAD")

    root = tmp_path / "parallax"
    (root / "evals").mkdir(parents=True)
    git(root, "init", "-q")
    git(root, "commit", "-q", "--allow-empty", "-m", "root")
    (root / "evals" / "cases.toml").write_text(f"""\
[[case]]
id = "calc-1"
repo = {json.dumps(str(up))}
base = "{base}"
fix = "{fix}"
tests = ["tests/test_calc.py"]
setup = []
goal = "add() returns the difference instead of the sum"
pr = "https://example.com/pr/1"
""")
    return root, evals.load_cases(root)[0]


def test_check_accepts_a_sound_case_and_flags_a_broken_one(world, tmp_path):
    root, case = world
    assert evals.check_case(case, tmp_path / "check") is None
    case.tests = ["tests/test_old.py"]  # passes before the fix, so it proves nothing
    assert evals.check_case(case, tmp_path / "check") == "the PR's tests already pass before the fix"


@pytest.mark.parametrize("write,verdict,outcome,judgment", [
    (FIXED, "pass", "resolved", "right"),
    ("def add(a, b):\n    return 0\n", "pass", "unresolved", "missed"),
    ("def add(a, b):\n    return 0\n", "fail", "unresolved", "caught"),
    (FIXED, "fail", "resolved", "false alarm"),
])
def test_run_case_scores_against_the_hidden_tests(world, tmp_path, write, verdict, outcome, judgment):
    root, case = world
    r = evals.run_case(case, tmp_path / "w", ScriptedAgent(steps=[("write", "calc.py", write)], cost=0.4),
                       FakeChecker(verdict=verdict))
    assert (r.outcome, r.judgment) == (outcome, judgment), r.reason
    assert r.cost_usd == pytest.approx(0.4) and r.diff_lines == 2 and r.human_diff_lines == 2
    assert r.inbox == (1 if verdict == "fail" else 0)


def test_the_maker_cant_see_the_future(world, tmp_path):
    root, case = world
    seen = {}

    def look(cwd):
        seen["hidden test present"] = (cwd / "tests" / "test_calc.py").exists()
        seen["fix commit present"] = subprocess.run(["git", "cat-file", "-e", case.fix], cwd=cwd).returncode == 0

    agent = ScriptedAgent(steps=[("call", look), ("write", "calc.py", FIXED)])
    checker = FakeChecker()
    evals.run_case(case, tmp_path / "w", agent, checker)
    assert seen == {"hidden test present": False, "fix commit present": False}
    goal, diff, _ = checker.calls[-1]
    assert "test_calc" not in agent.goals[-1][1] + goal + diff
    assert "You can run the tests with exactly: python -m pytest -q" in agent.goals[-1][1]


def test_the_eval_policy_allows_only_the_test_command(world, tmp_path):
    root, case = world
    agent = ScriptedAgent(steps=[("shell", ["python", "-m", "pytest", "-q"]), ("shell", ["git", "log"]),
                                 ("write", "calc.py", FIXED)])
    evals.run_case(case, tmp_path / "w", agent, FakeChecker())
    assert [p.allowed for _, _, p in agent.results] == [True, False, True]


def test_run_stops_at_the_budget_and_reports_everything(world, tmp_path, monkeypatch):
    root, case = world
    cases = [case, evals.Case(**{**case.__dict__, "id": "calc-2"})]
    out = evals.run(root, cases, make_maker=lambda cap: ScriptedAgent(steps=[("write", "calc.py", FIXED)], cost=2.4),
                    make_checker=lambda cap: FakeChecker(), budget=3.0, say=lambda s: None, run_id="t1")
    header, results = evals.read_run(out)
    assert header["run"] == "t1" and [r.outcome for r in results] == ["resolved", "skipped"]
    report = out.with_suffix(".md").read_text()
    assert "| resolved (the humans' tests pass) | 1 of 1 run |" in report
    assert "calc-2: skipped" in report and "budget reached" in report
    assert "1 of 1 real fixes resolved" in evals.summary_line(out)


def test_a_crashing_case_is_a_result(world, tmp_path):
    root, case = world
    case.fix = "0" * 40  # scoring can't read the hidden tests
    r = evals.run_case(case, tmp_path / "w", ScriptedAgent(), FakeChecker())
    assert r.outcome == "error" and r.reason


def test_cli_eval_needs_the_parallax_folder(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["eval", "check"]) == 1
    assert "run this from the parallax repo folder" in capsys.readouterr().err


def test_cost_shows_in_the_ledger_and_task_list(repo, monkeypatch, capsys):
    from parallax.runner import run_task
    proj = Project.init(repo)
    tid = proj.new_task("x")["task"]
    run_task(proj, tid, ScriptedAgent(cost=0.25), FakeChecker())
    assert proj.task(tid)["cost_usd"] == pytest.approx(0.25)
    monkeypatch.chdir(repo)
    main(["task", "list"])
    assert "$0.25" in capsys.readouterr().out


def test_task_new_says_what_to_do_next(repo, monkeypatch, capsys):
    Project.init(repo)
    monkeypatch.chdir(repo)
    main(["task", "new", "fix it"])
    assert "next: parallax run " in capsys.readouterr().out


def test_bare_parallax_prints_a_guide(capsys):
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "start here:" in out and 'parallax task new "what you want"' in out
