"""The card tells the truth about checks (real use, 2026-10-01: 7ac365, fb461d)."""
from fakes import FakeChecker
from parallax import progress, show, views
from parallax.agents.base import Finding, Review
from test_accept import ready
from test_check import approved, built, kinds, run


def test_when_none_of_reticles_tests_counted_the_strip_and_headline_say_so(repo):
    proj, tid, _ = ready(repo)
    proj.ledger.append("reticle.recorded", "reticle", "0 tests kept, 1 weak ones dropped.", task=tid, kept=[],
                       weak=[{"name": "test_reticle", "why": "the file doesn't load on the base (collection failure)"}])
    [ret] = [s for s in progress.strip(proj, tid) if s["stage"] == "reticle"]
    assert ret["state"] == "none"  # never a green tick (7ac365)
    bottom = show.report(proj, tid).splitlines()[1]
    assert bottom.endswith("plan tests pass; no Reticle tests counted.")
    assert views.card(proj, tid)["report"]["bottom"].endswith("no Reticle tests counted.")


def test_when_reticles_tests_counted_it_is_done_and_the_headline_is_as_before(repo):
    proj, tid, _ = ready(repo)
    proj.ledger.append("reticle.recorded", "reticle", "1 test kept.", task=tid, kept=[{"name": "test_outcome_1_x", "outcome": "1"}], weak=[])
    [ret] = [s for s in progress.strip(proj, tid) if s["stage"] == "reticle"]
    assert ret["state"] == "done"
    assert "no Reticle tests counted" not in show.report(proj, tid)


def _points():
    return [Finding("minor", "README.md:1", "the install line could name the Python version", "behavior"),
            Finding("nit", "README.md:2", "a trailing space", "housekeeping")]


def test_second_eyes_points_are_listed_right_under_its_line(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    assert run(proj, tid, maker, FakeChecker(reviews=[Review("pass", _points())])) == "ready"
    card = views.card(proj, tid)
    texts = [f["text"] for f in card["found"]]
    at = next(i for i, t in enumerate(texts) if t.startswith("Second Eye, the blind checker, passed it, with 2 points below"))
    assert texts[at + 1].startswith("Minor, at README.md:1: the install line could name the Python version")
    assert texts[at + 2].startswith("Nit, at README.md:2: a trailing space")


def test_the_points_stay_under_its_line_when_the_card_is_long(repo):
    """7ac365: the points went to Details, and the card said "with 2 points below" over nothing."""
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    long = [Finding("minor", f"README.md:{n}", "a long point " + "about the wording of this line " * 4, "behavior")
            for n in (1, 2)]
    assert run(proj, tid, maker, FakeChecker(reviews=[Review("pass", long)])) == "ready"
    card = views.card(proj, tid)
    texts = [f["text"] for f in card["found"]]
    assert sum(t.startswith("Minor, at README.md:") for t in texts) == 2
    assert not any(str(d).count("Minor, at README.md:") for d in card["details"])


def test_while_focus_redrafts_the_strip_shows_focus_working(repo):
    """fb461d: during the redraft, Focus showed "didn't run"."""
    proj, tid, _ = ready(repo)
    proj.ledger.append("task.redraft", "human", "keep everything else as it is", task=tid)
    assert progress.strip(proj, tid)[0]["state"] == "working"
    for doc in ("intent", "plan"):  # the live line now reads "checking the plan against the intent"
        proj.ledger.append("draft.recorded", "drafter", "", task=tid, doc=doc, sha="x")
    s = progress.strip(proj, tid)
    assert [(x["stage"], x["state"]) for x in s] == [("focus", "working"), ("reticle", "skipped"), ("maker", "skipped"),
                                                     ("check", "skipped"), ("ready", "skipped")]
    assert not kinds(proj, "gate.approved")[1:]  # nothing approved since the redraft
