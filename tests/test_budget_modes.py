"""Budget modes, chosen once and changeable: ask (the size caps), ceiling, or none. A budget named in
the request always wins, and every mode stops when the same check fails the same way twice."""
import math

import pytest

from fakes import FakeDrafter
from parallax import budgets, costs, decide, lifecycle, pilot, progress, show, views
from parallax.cli import main
from parallax.core import POLICY_FILE, Project
from parallax.policy import DEFAULT_POLICY, Policy, write_budget_mode
from test_lifecycle_gates import WANT, docs, make_key
from test_no_dead_ends import eight_dollar_docs, pilot_once

QUESTION = "How should Parallax handle spending?"


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch):
    from parallax import build
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)


def project(repo, mode=None):
    make_key()
    proj = Project.init(repo)
    if mode:
        budgets.choose(proj, mode)
    return proj


def drafted(proj, d=None):
    tid = pilot.intake(proj, WANT)["task"]
    pilot.draft_until_fit(proj, tid, FakeDrafter(d or docs()))
    return tid


# the question ----------------------------------------------------------------------------------------------

def test_a_new_repo_has_not_chosen_and_the_question_names_the_policys_numbers(repo):
    proj = project(repo)
    assert not proj.policy.mode_chosen and budgets.mode(proj) == "ask"
    s = budgets.state(proj)
    assert s["question"] == QUESTION and not s["chosen"]
    assert [(c["mode"], c["label"]) for c in s["choices"]] == [
        ("ask", "Ask me before a task goes over a limit"),
        ("ceiling", "Keep going, and stop only at $25 a task"),
        ("none", "No limit")]
    assert "at most $5 for a small task and $20 for a large one" in s["choices"][0]["does"]
    assert "same check fails the same way twice in a row" in s["choices"][2]["does"]
    assert "On a Claude subscription they measure how much a task used, not a charge." in s["note"]


def test_init_asks_once_in_a_terminal_and_writes_the_answer(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    make_key()
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert QUESTION in out and "  2. Keep going, and stop only at $25 a task:" in out
    proj = Project(repo)
    assert proj.policy.mode_chosen and budgets.mode(proj) == "ceiling"
    assert 'mode = "ceiling"' in (repo / POLICY_FILE).read_text()
    [e] = [e for e in proj.ledger.entries() if e["kind"] == "budget.mode"]
    assert e["actor"] == "human" and e["data"]["mode"] == "ceiling"
    asked = []
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "3")
    assert main(["init"]) == 0 and not asked  # once: it's answered


def test_init_outside_a_terminal_leaves_it_for_the_first_ui_open(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    make_key()
    assert main(["init"]) == 0
    assert "parallax ui asks how to handle spending when it first opens" in capsys.readouterr().out
    assert not Project(repo).policy.mode_chosen


def test_the_answer_is_written_into_the_policy_file_keeping_everything_else(tmp_path):
    path = tmp_path / POLICY_FILE
    path.write_text(DEFAULT_POLICY)
    write_budget_mode(path, "none")
    text = path.read_text()
    assert text.replace('mode = "none"\n', "", 1) == DEFAULT_POLICY  # one line added, nothing else touched
    write_budget_mode(path, "ceiling")
    assert text.replace('mode = "none"', 'mode = "ceiling"') == path.read_text()
    assert Policy.load(path).budget["mode"] == "ceiling"
    with pytest.raises(ValueError, match='mode must be "ask", "ceiling" or "none"'):
        write_budget_mode(path, "lots")
    (tmp_path / "other.toml").write_text("[launch]\nauto_launch_usd = 3.0\n")
    write_budget_mode(tmp_path / "other.toml", "ask")
    assert Policy.load(tmp_path / "other.toml").budget["mode"] == "ask"


def test_parallax_budget_shows_and_changes_the_mode(repo, monkeypatch, capsys):
    project(repo)
    monkeypatch.chdir(repo)
    assert main(["budget"]) == 0
    assert capsys.readouterr().out == ("Spending: ask me before a task goes over a limit (the default; nobody has chosen yet). "
                                       "Change it with parallax budget ask, ceiling or none, or [budget] mode in parallax.policy.toml.\n")
    assert main(["budget", "none"]) == 0
    assert capsys.readouterr().out.startswith("budget mode is none.\nSpending: no limit. Change it with")


def test_the_overview_and_the_page_show_the_mode(repo):
    from parallax.ui import act
    proj = project(repo)
    assert any(line.startswith("Spending: ask me before") for line in progress.overview(proj))
    assert act(proj, "/api/budget", {"mode": "ceiling"})["message"].startswith("budget mode is ceiling.")
    proj = Project(repo)
    assert budgets.state(proj)["chosen"] and budgets.state(proj)["mode"] == "ceiling"
    assert "Spending: keep going, and stop only at $25 a task." in " ".join(progress.overview(proj))
    from parallax.core import ParallaxError
    with pytest.raises(ParallaxError):
        act(proj, "/api/budget", {"mode": "lots"})


# what each mode does ---------------------------------------------------------------------------------------

def test_ask_mode_is_todays_behavior(repo):
    proj = project(repo, "ask")
    tid = drafted(proj)
    plan = lifecycle.plan_data(proj, tid)  # its cap, as Focus wrote it and code raised it for a rework
    assert costs.budget(proj, tid, plan)[0] == plan["budget_cap_usd"] < 3.0
    assert pilot.launch_rule(proj, tid) == (
        True, f"launch rule: a small task, cap ${plan['budget_cap_usd']:.2f} within auto_launch_usd $3.00, nothing in review_paths")


def test_ceiling_mode_keeps_going_to_the_ceiling(repo):
    proj = project(repo, "ceiling")
    tid = drafted(proj)
    cap, left = costs.budget(proj, tid, lifecycle.plan_data(proj, tid))
    assert cap == 25.0 and left == pytest.approx(25.0 - costs.spent(proj, tid))
    plan_cap = lifecycle.plan_data(proj, tid)["budget_cap_usd"]
    assert pilot.launch_rule(proj, tid) == (
        True, f"launch rule: a small task, cap ${plan_cap:.2f} within the budget mode's $25.00 ceiling, nothing in review_paths")
    assert budgets.limit(proj, tid) == 25.0  # a plan may set up to the ceiling, whatever its size


def test_no_limit_mode_never_stops_for_money_and_says_so(repo):
    proj = project(repo, "none")
    tid = drafted(proj)
    cap, left = costs.budget(proj, tid, lifecycle.plan_data(proj, tid))
    assert math.isinf(cap) and math.isinf(left)
    assert pilot.launch_rule(proj, tid)[1] == "launch rule: a small task, budget mode none (no spending limit), nothing in review_paths"
    assert progress.spend(proj, tid)["cap"] is None  # no bar against a cap that isn't there
    from parallax.agents.claude import _limit
    assert _limit(math.inf) is None and _limit(2.0) == 2.0  # the SDK gets None, never infinity


def test_a_budget_named_in_the_request_wins_in_every_mode(repo):
    proj = project(repo, "ceiling")
    d = docs()
    d["intent"] = d["intent"].replace("size: small\n", "size: small\nbudget: 2.00\n")
    tid = drafted(proj, d)
    assert costs.budget(proj, tid, lifecycle.plan_data(proj, tid))[0] == 2.0  # yours, not the $25 ceiling


def test_a_named_budget_over_the_modes_limit_asks_once_through_the_same_decision(repo):
    proj = project(repo, "ask")
    tid = pilot.intake(proj, WANT + " with a budget of $8")["task"]
    pilot_once(proj, tid, FakeDrafter(eight_dollar_docs()))
    dec = decide.decision(proj, tid)
    assert dec.kind == "budget" and dec.recommend == "allow and launch"
    budgets.choose(proj, "ceiling")  # $8 is under the $25 ceiling: nothing to ask about the budget, or the launch
    assert budgets.over_limit(Project(repo), tid) is None


def test_the_card_says_when_there_is_no_limit(repo):
    proj = project(repo, "none")
    proj.reload_policy()
    from parallax.core import POLICY_FILE as P
    text = (repo / P).read_text().replace("review_plans = false", "review_plans = true")
    (repo / P).write_text(text)
    proj.reload_policy()
    tid = drafted(proj)
    pilot.run(proj, tid, FakeDrafter(docs()), None, None)  # review_plans: the plan waits for you
    assert "with no spending limit (budget mode none)." in show.report(proj, tid)
    assert views.card(proj, tid)["spend"]["cap"] is None
