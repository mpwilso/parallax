"""`parallax eval --seeded`, with scripted agents: broken versions of the real fix, made by code, and
Second Eye and Reticle scored on them. No model, and no Maker at all."""
import json
import subprocess

import pytest

from fakes import FakeChecker, FakeDrafter, blocker
from parallax import evals, lint, reticle, seeded
from parallax.agents.base import Review
from parallax.core import Project
from test_evals import _commit, plain_runner
from test_lifecycle_gates import make_key
from test_reticle import FakeReticle

HIDDEN = "HIDDEN-SEEDED-TEST"
BASE = "def add(a, b):\n    return a - b\n\n\ndef clamp(x):\n    return x\n"
FIX = "def add(a, b):\n    return a + b\n\n\ndef clamp(x):\n    if x > 10:\n        return 10\n    return x\n"
INTENT = """\
Bottom line: Make add sum, and cap clamp at 10.
Not looked at: nothing

kind: bug
size: small
title: fixing add and clamp
scope: calc.py, tests/**

## Problem
add subtracts.

## Outcome
1. asked: add returns the sum of its arguments.
2. inferred: clamp caps values above 10 at 10.

## Constraints
Keep the names.
"""
TESTS = ("from calc import add, clamp\n\n\ndef test_outcome_1_sum():\n    assert add(2, 3) == 5\n\n\n"
         "def test_outcome_2_cap():\n    assert clamp(11) == 10\n")


@pytest.fixture
def upstream(tmp_path):
    repo = tmp_path / "upstream"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    old = "def test_exists():\n    pass\n"
    base = _commit(repo, {"calc.py": BASE, "tests/test_calc.py": old}, "base")
    fix = _commit(repo, {"calc.py": FIX, "tests/test_calc.py": old + (
        f"\n\ndef test_it():  # {HIDDEN}\n    from calc import add, clamp\n"
        "    assert add(2, 3) == 5 and clamp(11) == 10 and clamp(10) == 10 and clamp(3) == 3\n")}, "fix")
    return evals.Case(id="calc-2", repo=str(repo), base=base, fix=fix, tests=["tests/test_calc.py"], setup=[],
                      goal="add(2, 3) gives -1; it should give 5.")


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def run(proj, case, checker, writer, budget=5.0):
    said = []
    reticle.WRITER = writer  # conftest's monkeypatch puts the refusal back after the test
    out = seeded.run(proj, [case], budget, runner=plain_runner, say=said.append,
                     drafter_for=FakeDrafter({"intent": INTENT}, cost=0.05), checker_for=checker)
    return out, said


def test_broken_versions_are_made_by_code_and_kept_only_when_they_fail_the_hidden_tests(upstream):
    cache = evals.cache_repo(upstream)
    names = [n for n, _ in seeded.candidates(cache, upstream)]
    assert names == ["flip > to >= at calc.py:6", "change 10 to 11 at calc.py:6", "undo the hunk at calc.py:2",
                     "change 10 to 11 at calc.py:7", "undo the hunk at calc.py:6"]  # every kind, interleaved
    assert "tests/test_calc.py" not in seeded.fix_files(cache, upstream)  # the hidden tests are never part of a version


def test_second_eye_and_reticle_are_scored_on_each_broken_version_and_the_real_fix(proj, upstream):
    checker = FakeChecker(reviews=[blocker("wrong cap", "calc.py:6"), Review("pass"), Review("pass"), Review("pass")])
    writer = FakeReticle(TESTS, cost=0.03)
    out, said = run(proj, upstream, checker, writer)
    assert said[0].startswith("estimated $0.25 for 1 cases") and "no Maker" in said[0]
    r = json.loads((out / "calc-2.json").read_text())
    assert [v["broken"] for v in r["versions"]] == ["change 10 to 11 at calc.py:6", "undo the hunk at calc.py:2",
                                                    "change 10 to 11 at calc.py:7"]  # the flip passes the hidden tests: dropped
    assert [v["second_eye"] for v in r["versions"]] == ["catch", "miss", "miss"]
    assert [v["reticle"] for v in r["versions"]] == ["miss", "catch", "miss"]  # its asked test is of add
    assert [v["reticle_with_inferred"] for v in r["versions"]] == ["catch", "catch", "catch"]  # clamp's test too
    assert r["real_fix"] == {"second_eye": "right", "findings": [], "reticle": "right", "inferred_notes": 0}
    assert r["versions"][0]["findings"] == ["blocker: wrong cap"]  # what Second Eye said, kept with the result
    assert r["reticle_kept"] == {"asked": 1, "inferred": 1} and r["inferred"] == ["2"]
    assert r["cost_usd"] == pytest.approx(0.08)  # the intent and Reticle; the fake checker costs nothing
    # Second Eye's exact normal input, and never the hidden tests: in its brief or in Reticle's
    assert len(checker.briefs) == 4 and all(b.startswith("Outcome:\n1. add returns the sum") for b in checker.briefs)
    assert not any(HIDDEN in b or "test_calc.py" in b for b in checker.briefs) and HIDDEN not in writer.goals[0]
    text = (out / "summary.md").read_text()
    assert text.splitlines()[1] == ("Bottom line: On 3 broken versions, Second Eye caught 1 and Reticle 1; on 1 real "
                                    "fixes, Reticle raised 0 false alarms.")
    assert lint.lint_report(text, root=proj.root) == []


def test_a_reticle_test_that_fails_the_real_fix_is_a_false_alarm(proj, upstream):
    wrong = "from calc import add\n\n\ndef test_outcome_1_sum():\n    assert add(2, 3) == 6\n"
    out, _ = run(proj, upstream, FakeChecker(), FakeReticle(wrong))
    r = json.loads((out / "calc-2.json").read_text())
    assert r["real_fix"]["reticle"] == "false alarm" and r["real_fix"]["second_eye"] == "right"


def test_the_seeded_run_stops_before_a_case_the_budget_cant_cover(proj, upstream):
    out, said = run(proj, upstream, FakeChecker(), FakeReticle(TESTS), budget=1.0)
    assert said[-1] == "stopped before calc-2: $0.00 spent, and the next case needs up to $1.10 of the $1.00 budget."
    assert json.loads((out / "run.json").read_text())["done"] == []
