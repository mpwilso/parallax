"""Every stop names an action (real use, 2026-10-01: 786e71, fb461d, 89bc50)."""
import re
import shutil

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import build, decide, lifecycle, pilot, show, views
from parallax.agents.base import Review
from parallax.core import ParallaxError, Project
from test_check import approved, blocker, built, kinds, run
from test_lifecycle_gates import WANT, docs, make_key


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)  # nothing here starts a real background process


def reworked_to_ready(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "one\n")])
    maker.steps["build"] = [("write", "README.md", "two\n")]
    assert run(proj, tid, maker, FakeChecker(reviews=[blocker(), Review("pass")])) == "ready"
    return proj, tid, wt


def test_a_merged_card_after_rework_shows_without_its_worktree(repo):
    """89bc50: once its worktree was removed, the merged card failed on every refresh."""
    from parallax.ui import act
    proj, tid, wt = reworked_to_ready(repo)
    act(proj, "/api/accept", {"task": tid, "merge": True})
    assert proj.task(tid)["status"] == "merged"
    shutil.rmtree(wt)
    card = show.report(proj, tid)
    assert card.startswith("Type: FYI\nBottom line: Task ") and "you merged it unchanged" in card
    assert not views.card(proj, tid).get("broken")
    diff = views.document(proj, tid, "diff")
    assert "README.md" in diff and "+two" in diff  # from the project root: the commits are in the same repo


# a budget over the limit: one question, no redraft (786e71) ----------------------------------------------



def eight_dollar_docs():
    d = docs()
    d["intent"] = d["intent"].replace("size: small\n", "size: small\nbudget: 8.00\n")
    d["plan"] = d["plan"].replace("budget_cap_usd = 2.0", "budget_cap_usd = 8.0").replace("estimated_cost_usd = 0.9", "estimated_cost_usd = 2.0")
    return d


def pilot_once(proj, tid, drafter):
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    return build.run_mode(proj, tid, "pilot", drafter, lambda left, s: maker, FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=good_probe)


def over_limit_task(repo):
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT + " with a budget of $8")["task"]
    drafter = FakeDrafter(eight_dollar_docs())
    pilot_once(proj, tid, drafter)
    return proj, tid, drafter


def test_a_budget_over_the_limit_asks_once_and_never_redrafts(repo):
    proj, tid, drafter = over_limit_task(repo)
    assert len(drafter.requests) == 2 and not kinds(proj, "draft.misfit")  # one intent, one plan, no redraft
    dec = decide.decision(proj, tid)
    assert dec.kind == "budget" and dec.recommend == "allow and launch"  # $8 is over auto_launch_usd too: one answer
    assert dec.question == "Your budget of $8 is over the $5 limit for small tasks: allow $8 and launch it, or use $5?"
    assert [o.name for o in dec.options] == ["allow and launch", "allow", "use limit", "drop"]
    card = show.report(proj, tid)
    assert "Bottom line: Needs you: your budget of $8 is over the $5 limit for small tasks." in card
    assert f'Next: you run parallax decide {tid} "allow and launch".' in card


def test_allowing_the_budget_goes_on_without_new_drafts_and_the_launch_still_asks_you(repo):
    proj, tid, drafter = over_limit_task(repo)
    assert decide.apply(proj, tid, "allow") == f"drafting {tid} without you. it comes back to the inbox."
    [allowed] = kinds(proj, "budget.allowed")
    assert allowed["actor"] == "human" and allowed["data"]["amount_usd"] == 8.0
    pilot_once(proj, tid, drafter)
    assert len(drafter.requests) == 2  # the drafts there were, not new ones
    dec = decide.decision(proj, tid)
    assert (dec.kind, dec.question) == ("launch", "Launch it, with a cap of $8.00?")  # spending it is still your click
    assert not kinds(proj, "gate.approved")


def test_using_the_limit_sets_the_budget_and_cap_to_it_by_code(repo):
    proj, tid, drafter = over_limit_task(repo)
    decide.apply(proj, tid, "use limit")
    from parallax import lint
    assert lint.budget_of(lifecycle._read(proj, tid, "intent")) == 5.0
    assert lifecycle.plan_data(proj, tid)["budget_cap_usd"] == 5.0
    pilot_once(proj, tid, drafter)
    assert len(drafter.requests) == 2
    dec = decide.decision(proj, tid)
    assert (dec.kind, dec.question) == ("launch", "Launch it, with a cap of $5.00?")
    decide.apply(proj, tid, "launch")  # the files code wrote are the ones approved: their hashes match
    [gate] = kinds(proj, "gate.approved")
    assert gate["actor"] == "human" and gate["data"]["cap_usd"] == 5.0


# a redraft never lowers the cap you approved (fb461d: $3.80, $3.60, $2.50) --------------------------------

def test_a_redraft_never_lowers_the_cap_you_approved(repo):
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    first = docs()
    first["plan"] = first["plan"].replace("budget_cap_usd = 2.0", "budget_cap_usd = 2.8")
    pilot_once(proj, tid, FakeDrafter(first))
    assert proj.task(tid)["status"] == "ready"
    [gate] = kinds(proj, "gate.approved")
    assert gate["data"]["cap_usd"] == 2.8
    pilot.redraft(proj, tid, "do it again, keep everything else as it is")
    pilot_once(proj, tid, FakeDrafter(docs()))  # Focus writes a smaller cap: $2.00
    assert lifecycle.plan_data(proj, tid)["budget_cap_usd"] == 2.8
    raised = [e for e in kinds(proj, "draft.recorded") if e["data"].get("floor") == "approved"]
    assert raised and "you approved $2.80 before this redraft" in raised[-1]["reason"]
    assert kinds(proj, "gate.approved")[-1]["data"]["cap_usd"] == 2.8


def test_an_older_approval_without_its_cap_reads_the_plan_kept_at_the_reject(repo):
    from parallax import budgets
    from parallax.since import _kept
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("gate.approved", "human", "", task=tid, gate="intent+plan", files={}, sig="")  # as before today
    proj.ledger.append("budget.raised", "human", "more", task=tid, amount_usd=0.5)
    proj.ledger.append("task.redraft", "human", "again", task=tid)
    kept = _kept(proj, tid, 1)
    kept.mkdir(parents=True)
    (kept / "plan.md").write_text(docs()["plan"].replace("budget_cap_usd = 2.0", "budget_cap_usd = 3.3"))
    assert budgets.approved_cap(proj, tid) == 3.8


# every stop names an action: the walk ----------------------------------------------------------------------

STOPS = [  # (ledger kind, data, reason): one per kind of stop the card, the inbox and parallax show can produce
    ("disagreement.raised", {"stage": "scope"}, "extra.py changed but isn't in the plan's files"),
    ("disagreement.raised", {"stage": "scope", "files": [{"path": ".env", "secret": True, "size": 3}]}, ".env has content"),
    ("disagreement.raised", {"stage": "check"}, "the check still fails after 3 rework cycles: tests"),
    ("disagreement.raised", {"stage": "check"}, "Second Eye error: garbled reply"),
    ("disagreement.raised", {"stage": "check", "loop": True}, "the same check failed the same way twice in a row: failing tests in test_x"),
    ("disagreement.raised", {"stage": "check"}, "the plan's tests couldn't run (exit 4): no module"),
    ("disagreement.raised", {"stage": "guard"}, "diff touches protected files: CLAUDE.md"),
    ("disagreement.raised", {"stage": "conflict"}, "the plan says X, the intent says Y"),
    ("disagreement.raised", {"stage": "conflict", "missing": ["tests/test_x.py"]}, "the plan's test file is missing"),
    ("disagreement.raised", {"stage": "flows"}, "Field's test x still fails after a rework. Either the test or the app is wrong"),
    ("stuck.raised", {}, "drafting still failed after 2 redrafts: the plan lists setup.py"),
    ("stuck.raised", {}, "the same call was refused 3 times: shell.run curl"),
    ("stuck.raised", {"error": True}, "error: the sandbox runtime exited with code 1 (srt: bwrap: namespace)"),
    ("stuck.raised", {"missing_tool": "uv", "fix": "install it"}, "uv not found: the [build] setup command needs it"),
    ("stuck.raised", {"turns": True}, "Maker used all 80 turns"),
    ("stuck.raised", {"budget": True}, "Maker spent $3.10 of the $3.80 cap, leaving Field $0.20, so the budget cap ran out"),
    ("stuck.raised", {"over_limit": True, "named": 8.0, "limit": 5.0, "size": "small"},
     "your budget of $8 is over the $5 limit for small tasks"),
    ("review.requested", {}, "your policy reviews every plan (review_plans)"),
    ("review.requested", {}, "the budget cap ($4.00) is over auto_launch_usd ($3.00)"),
]


def _actions(proj, tid, why):
    """The card's buttons. A Needs you card with none is a dead end: the walk fails on it."""
    card = views.card(proj, tid)
    if card["state"] != "needs you":
        return []
    acts = card["actions"]
    if acts.get("kind") != "decide" or not acts.get("options"):
        return [f"{card['status']} card has no actions: {why}"]
    return []


def _quoted(name):
    return f'"{name}"' if " " in name else name


def test_every_kind_of_stop_names_what_happened_why_and_one_action(repo):
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    pilot_once(proj, tid, FakeDrafter(docs()))
    seen, missing = set(), []
    for kind, data, why in STOPS:
        if kind == "review.requested":  # waiting on a plan review or a launch: the task itself says so
            proj.ledger.append("task.redraft", "human", "a fresh attempt for the next case", task=tid)
            pilot.draft_until_fit(proj, tid, FakeDrafter(docs()))
        item = proj.ledger.append(kind, "parallax", why, task=tid, **data)
        dec = decide.decision(proj, tid)
        seen.add(dec.kind)
        card = show.report(proj, tid).splitlines()
        rec = dec.option(dec.recommend)
        happened = card[1].removeprefix("Bottom line: Needs you: ")
        action = f"parallax decide {tid} {_quoted(rec.name)}"
        if not (happened and card[3].startswith(f"Next: you run {action}") and f"- Why a human: {dec.why_human}." in card
                and rec.does):
            missing.append(f"{dec.kind}: {why}: " + " | ".join(card[:9]))
        row = next(i for i in views.board(proj)["waiting"] if i["task"] == tid)
        if not row["line"]:
            missing.append(f"{dec.kind} row: {why}")
        missing += _actions(proj, tid, why)
        if kind != "review.requested":
            proj.resolve(item["id"], False, "next case")
    assert not missing, "these stops name no action: " + "; ".join(missing)
    assert seen == set(decide.WHY_HUMAN), f"add a case for: {sorted(set(decide.WHY_HUMAN) - seen)}"


AFTERMATHS = [  # what's left once a stop is answered and nothing started: (stop, its data, the answer)
    ("stuck.raised", {}, True),                        # retry chosen, then the restart failed: status "open"
    ("stuck.raised", {"error": True}, True),
    ("disagreement.raised", {"stage": "check"}, False),  # sent back, then the redraft never started: "needs work"
    ("disagreement.raised", {"stage": "conflict"}, True),  # the plan won: "plan approved"
    ("disagreement.raised", {"stage": "scope"}, True),   # the risk accepted: "risk accepted"
]


def test_a_needs_you_card_always_has_an_action_even_after_an_answer_that_went_nowhere(repo):
    """A failed setup retry left the card on Needs you with no buttons."""
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    pilot_once(proj, tid, FakeDrafter(docs()))
    missing = []
    for kind, data, answer in AFTERMATHS:
        item = proj.ledger.append(kind, "parallax", "it stopped", task=tid, **data)
        proj.resolve(item["id"], answer, "an answer whose follow-up never started")
        proj.ledger.append("builder.finished", "parallax", "", task=tid, status="stuck")  # nothing is running
        missing += _actions(proj, tid, f"{kind} {data} answered {answer}")
        for status in ("maker failed", "blocked", "over budget", "disputed"):
            proj.ledger.append("build.finished", "parallax", "", task=tid, status=status)
            missing += _actions(proj, tid, f"build.finished {status}")
    assert not missing, "; ".join(missing)


def test_a_failed_setup_retry_comes_back_with_its_actions(repo):
    from parallax.core import POLICY_FILE
    (repo / POLICY_FILE).write_text('[build]\nsetup = "echo the mirror is down >&2; exit 3"\n')
    make_key()
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_once(proj, tid, FakeDrafter(docs())) == "stuck"
    assert decide.decision(proj, tid).kind == "error"
    with pytest.raises(ParallaxError) as err:
        decide.apply(proj, tid, "retry")  # setup fails again, in the retry itself
    assert "the [build] setup command failed: the mirror is down" in str(err.value)
    assert "back in the inbox" in str(err.value)
    card = views.card(proj, tid)
    assert card["state"] == "needs you" and card["actions"]["kind"] == "decide"
    assert [o["name"] for o in card["actions"]["options"]] == ["retry", "reject", "drop"]
    report = show.report(proj, tid)
    assert "the mirror is down" in report and f"Next: you run parallax decide {tid} " in report


def test_a_retry_that_hits_a_missing_program_names_it(repo, monkeypatch):
    from parallax import tools
    proj, tid, wt = approved(repo)
    proj.ledger.append("stuck.raised", "parallax", "it stopped", task=tid)

    def lacking(*a, **k):
        raise tools.MissingTool(tools.Missing("uv", "the [build] setup command"))

    monkeypatch.setattr(pilot, "resume", lacking)
    with pytest.raises(ParallaxError):
        decide.apply(proj, tid, "retry")
    dec = decide.decision(proj, tid)
    assert dec.kind == "tool" and [o.name for o in dec.options] == ["retry", "drop"]


def test_stops_after_accept_and_a_broken_card_name_an_action_too(repo, monkeypatch):
    from parallax import accept as acc_mod
    from test_accept import Runner, _with_merge_tests, git, ready
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", Runner(0, "ok"))
    acc_mod.accept(proj, tid, merging=True)
    git(repo, "checkout", "-qb", "elsewhere")  # the checkout isn't on the base branch any more
    target = kinds(proj, "task.accepted")[-1]["data"]["target"]
    with pytest.raises(ParallaxError) as err:
        acc_mod.merge_now(proj, tid)
    assert str(err.value).endswith(f"not {target}. run git checkout {target}, then the merge command below")
    card = views.card(proj, tid)
    assert card["merge_note"].startswith(f"Accepted, but Accept and merge stopped: can't merge from here: your checkout is "
                                         f"on elsewhere, not {target}. run git checkout {target}")
    assert card["merge_note"].endswith("Run this in your repo's folder:") and card["merge"]
    _, bad, _ = approved(repo)
    proj.ledger.append("stuck.raised", "parallax", "the change goes outside the plan", task=bad, stage="scope",
                       files=[{"path": "a.txt"}, {"path": "b.txt"}])  # deliberately broken: no cause or size
    broken = views.card(proj, bad)
    assert broken["broken"] and broken["report"]["bottom"] == (
        f"Task {bad}: couldn't display this task. To see why, run parallax show {bad} in a terminal; every other task still works.")


# one question when you allow a higher budget ------------------------------------------------------------

def test_over_auto_launch_the_budget_question_offers_allow_and_launch_as_one_answer(repo):
    proj, tid, drafter = over_limit_task(repo)  # $8 named, auto_launch_usd is $5
    dec = decide.decision(proj, tid)
    assert dec.question == "Your budget of $8 is over the $5 limit for small tasks: allow $8 and launch it, or use $5?"
    assert [o.name for o in dec.options] == ["allow and launch", "allow", "use limit", "drop"]
    assert dec.recommend == "allow and launch" and dec.option("allow and launch").label == "Allow $8 and launch"
    assert views.card(proj, tid)["actions"]["options"][0]["label"] == "Allow $8 and launch"
    decide.apply(proj, tid, "allow and launch")
    pilot_once(proj, tid, drafter)
    assert len(drafter.requests) == 2 and not kinds(proj, "review.requested")  # no second stop to launch
    [gate] = kinds(proj, "gate.approved")
    [allowed] = kinds(proj, "budget.allowed")
    assert gate["actor"] == "human" and gate["data"]["answer"] == allowed["id"] and "rule" not in gate["data"]
    assert proj.task(tid)["status"] == "ready"
    from parallax import stats
    assert stats.touches(proj)[tid] == 1  # your one answer; the launch it carries out adds none


def test_allow_and_launch_asks_again_if_the_plan_changed_after_your_answer(repo):
    proj, tid, drafter = over_limit_task(repo)
    decide.apply(proj, tid, "allow and launch")
    plan = lifecycle.doc_path(proj, tid, "plan")
    plan.write_text(plan.read_text().replace("## Steps", "## Steps\n0. Something you never read."))
    pilot_once(proj, tid, drafter)
    assert not kinds(proj, "gate.approved") and decide.decision(proj, tid).kind == "launch"


def test_under_auto_launch_the_budget_question_is_as_before(repo, monkeypatch):
    proj, tid, drafter = over_limit_task(repo)
    from parallax.core import POLICY_FILE
    text = (proj.root / POLICY_FILE).read_text()
    (proj.root / POLICY_FILE).write_text(re.sub(r"(?m)^auto_launch_usd\s*=\s*[0-9.]+", "auto_launch_usd = 10.00", text))
    proj.reload_policy()
    dec = decide.decision(proj, tid)
    assert [o.name for o in dec.options] == ["allow", "use limit", "drop"] and dec.recommend == "allow"
    assert dec.question == "Your budget of $8 is over the $5 limit for small tasks: allow $8, or use $5?"
