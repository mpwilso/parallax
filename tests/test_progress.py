"""Where each task stands, for the UI: rows, the stage strip, done rows and the overview."""
import pytest

from parallax import decide, progress, views
from test_accept import ready
from test_check import approved


@pytest.mark.parametrize("text,limit,out", [
    ("the budget cap ran out", 110, "The budget cap ran out."),                      # a whole sentence, as is
    ("Second Eye passed.", 110, "Second Eye passed."),
    ("the check still fails after 3 rework cycles: tests, flows and more", 40, "The check still fails after 3 rework…"),
    ("one two three, four five", 17, "One two three…"),                          # never after a dangling comma
])
def test_a_rows_summary_is_a_whole_sentence_or_cut_at_a_word_with_an_ellipsis(text, limit, out):
    said = progress.sentence(text, limit)
    assert said == out and len(said) <= limit
    assert not said.endswith((",…", ":…", " …"))


@pytest.mark.parametrize("state,kind,chip", [
    ("building", None, "Working"), ("ready", "ready", "Ready"), ("needs you", "cap", "Failed"),
    ("needs you", "rework", "Failed"), ("needs you", "scope", "Needs you"), ("needs you", "launch", "Needs you"),
])
def test_the_status_chip(state, kind, chip):
    assert progress.chip(state, kind) == chip


def test_the_strip_is_five_stages_and_a_stop_falls_on_the_stage_that_was_running(repo):
    proj, tid, _ = ready(repo)
    s = progress.strip(proj, tid)
    assert [(x["stage"], x["state"]) for x in s] == [("focus", "done"), ("reticle", "skipped"), ("maker", "done"),
                                                     ("check", "done"), ("ready", "done")]
    proj2, tid2, _ = approved(repo)  # a second task in the same repo
    proj2.ledger.append("tests.recorded", "parallax", "", task=tid2, tree="t", exit=1, per_file={"a.py": [1, 2, 0]}, passed=1, total=2)
    proj2.ledger.append("stuck.raised", "parallax", "the budget cap ran out ($2.30 of $2.20 estimated)", task=tid2, budget=True)
    s2 = progress.strip(proj2, tid2, decide.decision(proj2, tid2))
    assert [x["state"] for x in s2] == ["done", "skipped", "skipped", "failed", "skipped"]  # it stopped in the check


def test_done_rows_and_the_overview_say_it_in_plain_words(repo):
    from parallax.accept import accept
    proj, tid, _ = ready(repo)
    accept(proj, tid)
    [row] = views.board(proj)["done"]
    assert (row["outcome"], row["touches"]) == ("Accepted, waiting for your merge", 2) and row["date"]  # approve, accept
    lines = progress.overview(proj)
    assert lines[0] == "1 task finished. It needed you 2 times; the goal is once."
    assert lines[1].startswith("$") and lines[1].endswith("spent on agents in the last 7 days.")
    assert lines[-1].startswith("fixing the README install steps: accepted, waiting for your merge on ")
    assert all(line.endswith(".") for line in lines)
