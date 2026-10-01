"""Protected docs don't go stale silently (real use: a change made a protected doc wrong, Maker
couldn't edit it, and nothing told the person)."""
import subprocess

import pytest

from fakes import FakeChecker, FakeDrafter, Finding, Review
from parallax import pilot, show, staledocs, views
from parallax.core import Project
from test_lifecycle_gates import WANT, docs, make_key
from test_no_dead_ends import pilot_once

@pytest.fixture(autouse=True)
def no_spawn(monkeypatch):
    from parallax import build
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)  # nothing here starts a real background process


SECTION = ("## Docs you'll need to update\n"
           "- CLAUDE.md: it says the card shows only the last lines of a failing check\n"
           "- README.md: not protected, so not yours to chase here\n"
           "- docs/tasks/abc/plan.md: a task draft, never a doc to update\n\n")


def with_protected_docs(repo):
    (repo / "CLAUDE.md").write_text("# Rules\n\nThe card shows only the last lines of a failing check.\n")
    (repo / "docs").mkdir(exist_ok=True)
    (repo / "docs" / "parallax.md").write_text("# The output shape\n")
    (repo / "README.md").write_text("# readme\n")
    subprocess.run(["git", "-C", str(repo), "add", "CLAUDE.md", "docs/parallax.md", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "docs"], check=True)
    make_key()
    return Project.init(repo)


def stale_plan():
    d = docs()
    d["plan"] = d["plan"].replace("```toml", SECTION + "```toml")
    return d


def test_focus_is_told_which_docs_are_protected_and_where_to_list_them(repo):
    proj = with_protected_docs(repo)
    assert staledocs.protected(proj.root) == ["CLAUDE.md", "docs/parallax.md"]
    tid = pilot.intake(proj, WANT)["task"]
    drafter = FakeDrafter(docs())
    pilot_once(proj, tid, drafter)
    [plan_request] = [r for r in drafter.requests if "/plan.md in exactly this shape" in r]
    assert "These docs are protected: Maker can't edit them, so the person updates them by hand: CLAUDE.md, docs/parallax.md." in plan_request
    assert "## Docs you'll need to update\n- <path>: <what is wrong in it>" in plan_request
    assert "These docs are protected" not in drafter.requests[0]  # the intent's drafter isn't asked


def test_the_plans_section_names_only_protected_docs():
    plan = stale_plan()["plan"]
    assert [(p, why) for p, why, _ in staledocs.listed(plan)] == [
        ("CLAUDE.md", "it says the card shows only the last lines of a failing check")]
    assert staledocs.listed(docs()["plan"]) == []  # no section, nothing to update


def test_outside_a_git_repository_there_are_no_protected_docs(tmp_path):
    assert staledocs.protected(tmp_path) == [] and staledocs.material(tmp_path) == ""


def test_the_ready_card_lists_each_doc_to_update_yourself_before_merging(repo):
    proj = with_protected_docs(repo)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_once(proj, tid, FakeDrafter(stale_plan())) == "ready"
    line = ("CLAUDE.md: update this yourself before merging. It says the card shows only the last lines of a "
            "failing check.")
    report = show.report(proj, tid)
    assert f"- {line} (docs/tasks/{tid}/plan.md:" in report
    assert "README.md: update this yourself" not in report
    found = [f["text"] for f in views.card(proj, tid)["found"]]
    assert line in found


def test_second_eye_is_told_a_stale_protected_doc_is_a_note(repo):
    from parallax.agents import claude
    rule = " ".join(claude.BLIND_PROMPT.split())
    assert "Some docs are protected: the author can't edit them" in rule
    assert "is a note at a severity that doesn't block, never a reason to fail." in rule


def test_a_blocker_on_a_protected_doc_is_lowered_to_a_note_by_code(repo):
    proj = with_protected_docs(repo)
    tid = pilot.intake(proj, WANT)["task"]
    stale = Review("fail", [Finding("blocker", "CLAUDE.md:3", "CLAUDE.md still describes the old card")], "nothing")
    from fakes import ScriptedAgent, good_probe, junit_runner
    from parallax import build
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    status = build.run_mode(proj, tid, "pilot", FakeDrafter(stale_plan()), lambda left, s: maker,
                            FakeChecker(reviews=[stale]), test_runner=junit_runner(), preflight_runner=good_probe)
    assert status == "ready" and len(maker.goals) == 1  # no rework: Maker can't edit it anyway
    [down] = [e for e in proj.ledger.entries() if e["kind"] == "verdict.downgraded"]
    assert down["data"]["lowered"][0]["where"] == "CLAUDE.md:3" and down["data"]["lowered"][0]["to"] == "minor"
