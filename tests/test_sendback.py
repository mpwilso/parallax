"""A send-back changes only what the reason asks (real use: fb461d, 2026-10-01)."""
import pytest

from fakes import FakeDrafter
from parallax import build, lifecycle, pilot, sendback
from test_decide_and_redraft import pilot_run
from parallax.core import Project
from test_lifecycle_gates import WANT, docs, kinds, make_key

# fb461d's intent as it was sent back the second time (its kept copy), and the reason, as typed
BEFORE = """\
Bottom line: Notify when a task is ready or needs you.
Not looked at: nothing

kind: feature
size: small
title: notifying when a task is ready or needs you
scope: parallax/web/app.js, tests/test_ui_browser.py

## Problem
Work that waits on you doesn't find you.

## Outcome
1. asked: Opening the page from a link that names a task shows that task's card, once the board loads. The access token in the link still works.
2. inferred: A link that names a task that does not exist leaves the board usable and shows the usual error message. It does not leave a blank card.
3. asked: When a task changes to Ready or Needs you, the page creates a browser notification for it. (not browser-testable)
6. asked: Clicking the notification opens the page at that task's card, using the link from outcome 1. (not browser-testable)
9. asked: No new setting, toggle or control appears on the page.

## Constraints
- A browser without `window.Notification` works as it does today.
- The access token stays in the URL fragment and is never sent to a server.
- Notification behavior is proved in `tests/test_ui_browser.py` with a stubbed `window.Notification`. The stub covers creation, the click, and the permission request. Real OS notifications and permission prompts are not driven.
"""
REASON = ("Pin the link format in outcome 1: `#TOKEN&task=TASKID`, with both the token and the task id after the #, so "
          "nothing about the task is sent to the server. Keep everything else in the intent as it is.")
PINNED = ("1. asked: Opening the page from a link of the form `#TOKEN&task=TASKID` shows that task's card, once the board "
          "loads. The access token in the link still works.")
OLD_1 = BEFORE.splitlines()[12]


def redrafted(drop_markers=True, drop_stub=True, drop_scope=True):
    after = BEFORE.replace(OLD_1, PINNED)  # what the reason asked for
    if drop_markers:
        after = after.replace(" (not browser-testable)", "")
    if drop_stub:
        after = "\n".join(line for line in after.splitlines() if "stubbed" not in line) + "\n"
    if drop_scope:
        after = after.replace("scope: parallax/web/app.js, tests/test_ui_browser.py", "scope: parallax/web/app.js")
    return after


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def test_fb461ds_redraft_fails_on_what_it_dropped_and_names_each():
    found = sendback.problems(BEFORE, redrafted(), REASON)
    assert found == [
        'outcome 3 read "asked: When a task changes to Ready or Needs you, the page creates a browser notification for '
        'it. (not browser-testable)" before this redraft, and the human\'s reason doesn\'t ask to change it: put it back '
        "word for word",
        'outcome 6 read "asked: Clicking the notification opens the page at that task\'s card, using the link from '
        'outcome 1. (not browser-testable)" before this redraft, and the human\'s reason doesn\'t ask to change it: put '
        "it back word for word",
        'the constraint "Notification behavior is proved in `tests/test_ui_browser.py` with a stubbed '
        '`window.Notification`. The stub covers creation, the click, and the permission request. Real OS notifications '
        'and permission prompts are not driven." is gone or rewritten, and the human\'s reason doesn\'t ask to change '
        "it: put it back word for word",
        "the scope lost tests/test_ui_browser.py, and the human's reason doesn't ask to change the scope: put it back"]


def test_changing_only_what_the_reason_asks_passes():
    assert sendback.problems(BEFORE, redrafted(False, False, False), REASON) == []


@pytest.mark.parametrize("said,nums", [
    ("Mark outcomes 1 to 4 not browser-testable", {"1", "2", "3", "4"}),
    ("outcomes 2, 3 and 5 need the marker", {"2", "3", "5"}),
    ("fix outcome 6", {"6"}),
    ("outcomes 7-8 are fine", {"7", "8"}),
])
def test_the_outcomes_a_reason_names(said, nums):
    assert sendback._numbers(said) == nums


def test_a_reason_that_names_the_part_lets_it_change():
    after = redrafted(drop_markers=False, drop_stub=True, drop_scope=True)
    said = "pin the link format in outcome 1, drop the stub constraint and narrow the scope to app.js"
    assert sendback.problems(BEFORE, after, said) == []


def test_focus_gets_the_version_sent_back_and_restores_what_it_dropped(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    pilot_run(proj, tid)
    assert proj.task(tid)["status"] == "ready"
    pilot.redraft(proj, tid, "Make the outcome say WSL 2. Keep everything else as it is.")
    good = docs()["intent"].replace("A new user on WSL can follow them.", "A new user on WSL 2 can follow them.")
    dropped = good.replace("## Constraints\nKeep the macOS steps.\n", "## Constraints\nNone.\n")
    drafter = FakeDrafter({"intent": [dropped, good], "plan": docs()["plan"]})
    assert pilot.draft_until_fit(proj, tid, drafter) == "fit"
    first, second = drafter.requests[0], drafter.requests[2]
    assert "Your previous draft of this file, which the human sent back (data):" in first
    assert "Keep the macOS steps." in first and "Change only what the human's reason asks for." in first
    assert ('the constraint "Keep the macOS steps." is gone or rewritten, and the human\'s reason doesn\'t ask to '
            "change it: put it back word for word") in second
    assert "Keep the macOS steps." in lifecycle._read(proj, tid, "intent")
    assert len([e for e in kinds(proj, "draft.recorded") if e["data"]["doc"] == "intent"]) == 3  # first run, then two



def test_adding_to_a_line_keeps_it_and_taking_a_marker_off_does_not():
    grown = BEFORE.replace("works as it does today.", "works as it does today. Safari too.")
    assert sendback.problems(BEFORE, grown, "mention Safari") == []
    unmarked = BEFORE.replace("for it. (not browser-testable)", "for it.")
    assert [p.split(" read ")[0] for p in sendback.problems(BEFORE, unmarked, "mention Safari")] == ["outcome 3"]
