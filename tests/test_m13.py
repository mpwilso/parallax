import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import build, costs, decide, inbox, lifecycle, lint, pilot, preflight, show, stats, status
from parallax.accept import accept
from parallax.agents.base import Finding, Review
from parallax.cli import main
from parallax.core import POLICY_FILE, ParallaxError, Project
from test_m8 import WANT, docs, make_key


@pytest.fixture
def proj(repo, monkeypatch):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: SPAWNED.append(argv) or 9)
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    SPAWNED.clear()
    return Project.init(repo)


SPAWNED: list[list[str]] = []


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


def pilot_run(proj, tid, drafter=None, maker=None, checker=None, runner=None):
    maker = maker or ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    return build.run_mode(proj, tid, "pilot", drafter or FakeDrafter(docs()), lambda left, settings: maker,
                          checker or FakeChecker(), test_runner=runner or junit_runner(), preflight_runner=good_probe)


def lints(proj, text):
    return lint.lint_report(text, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []


# reject at Ready: a redraft from your reason -------------------------------------------------------------

def test_reject_at_ready_redrafts_intent_and_plan_from_your_reason(proj, monkeypatch, capsys):
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "ready"
    monkeypatch.chdir(proj.root)
    assert main(["reject", tid, "--reason", "say which shell each step runs in"]) == 0
    assert capsys.readouterr().out == f"redrafting {tid} from your reason. it comes back to the inbox.\n"
    assert SPAWNED[-1][-1] == "pilot" and inbox.items(proj) == []  # working without you again
    main(["diff", tid])
    assert capsys.readouterr().out == "no changes\n"  # the old attempt's change is gone, and not shown

    changed = {**docs(), "intent": docs()["intent"].replace("Keep the macOS steps.", "Keep the macOS steps. Name the shell.")}
    drafter = FakeDrafter(changed)
    assert pilot_run(proj, tid, drafter) == "ready"
    assert all("say which shell each step runs in" in r for r in drafter.requests)  # intent and plan both hear it
    assert "Name the shell." in lifecycle.doc_path(proj, tid, "intent").read_text()  # the intent was redrafted too
    attempt = status.attempt(proj.ledger.entries(), tid)
    assert [e["data"]["doc"] for e in attempt if e["kind"] == "draft.recorded"] == ["intent", "plan"]
    assert [e["actor"] for e in attempt if e["kind"] == "gate.approved"] == ["parallax"]  # launched by the rule again

    accept(proj, tid)
    assert stats.touches(proj)[tid] == 2  # the reject, and the accept


def test_each_attempt_gets_a_fresh_cap(proj):
    tid = pilot.intake(proj, WANT)["task"]
    pilot_run(proj, tid, maker=ScriptedAgent(steps=[("write", "README.md", "ok\n")], cost=1.5))
    plan = lifecycle.plan_data(proj, tid)
    assert costs.budget(proj, tid, plan)[1] == pytest.approx(0.3)  # 0.2 drafting + 1.5 build of the 2.00 cap
    pilot.redraft(proj, tid, "start over")
    assert costs.budget(proj, tid, plan) == (2.0, 2.0)
    assert proj.task(tid)["cost_usd"] == pytest.approx(1.7)  # the task's total still counts everything


def test_a_reject_needs_a_reason(proj, monkeypatch, capsys):
    tid = pilot.intake(proj, WANT)["task"]
    pilot_run(proj, tid)
    monkeypatch.chdir(proj.root)
    assert main(["reject", tid]) == 1
    assert "a reject needs a reason" in capsys.readouterr().err
    assert kinds(proj, "task.redraft") == []


# one question, its options, a recommendation --------------------------------------------------------

def test_a_plan_under_review_is_one_question(repo, monkeypatch, capsys):
    (repo / POLICY_FILE).write_text('[launch]\nreview_paths = ["README.md"]\n')
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: SPAWNED.append(argv) or 9)
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "needs you"
    card = show.report(proj, tid)
    assert "Decisions\n- Decide: Does the plan do what you want? Recommend: approve. Blocks: the build." in card
    assert f"Next: you run parallax decide {tid} with approve, reject or drop." in card
    assert "the checker" not in card.split("Found")[0]  # a plan under review hasn't met the checker
    assert lints(proj, card)
    monkeypatch.chdir(proj.root)
    assert main(["decide", tid, "approve"]) == 0
    assert capsys.readouterr().out == f"building {tid} without you. it comes back to the inbox.\n"
    assert SPAWNED[-1][-1] == "build" and kinds(proj, "gate.approved")[-1]["actor"] == "human"


def test_a_launch_over_the_threshold_asks_only_about_cost(repo, monkeypatch):
    (repo / POLICY_FILE).write_text('[launch]\nauto_launch_usd = 1.0\n')
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    proj = Project.init(repo)
    tid = pilot.intake(proj, WANT)["task"]
    pilot_run(proj, tid)
    dec = decide.decision(proj, tid)
    assert (dec.kind, dec.question, [o.name for o in dec.options]) == (
        "launch", "Launch it, with a cap of $2.00?", ["launch", "drop"])
    card = show.report(proj, tid)
    assert "cost: estimated $0.90, cap $2.00" in card and "the work: fixing the README install steps" in card
    assert lints(proj, card)
    assert decide.apply(proj, tid, "launch").startswith("building")


def test_a_reached_cap_can_be_raised_and_the_task_picks_up(proj):
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid, maker=ScriptedAgent(steps=[("write", "README.md", "ok\n")], cost=1.9)) == "stuck"
    dec = decide.decision(proj, tid)
    assert dec.kind == "cap" and dec.recommend == "raise" and dec.question == "Raise the cap to $3.00 so it can finish?"
    card = show.report(proj, tid)
    assert "Decide: Raise the cap to $3.00 so it can finish? Recommend: raise." in card and lints(proj, card)
    assert decide.apply(proj, tid, "raise") == f"checking {tid} without you. it comes back to the inbox."
    assert costs.budget(proj, tid, lifecycle.plan_data(proj, tid))[0] == pytest.approx(3.0)
    assert kinds(proj, "budget.raised")[0]["actor"] == "human" and proj.inbox() == []


@pytest.mark.parametrize("answer,then", [("intent", "pilot"), ("plan", "check")])
def test_intent_against_plan_is_yours_to_settle_either_way(proj, answer, then):
    tid = pilot.intake(proj, WANT)["task"]
    scope = Finding("major", "tests/test_readme.py", "the intent says no new tests", kind="scope")
    maker = ScriptedAgent(steps=[("call", lambda cwd: (cwd / "tests").mkdir(exist_ok=True)),
                                 ("write", "tests/test_readme.py", "def test(): pass\n")])
    assert pilot_run(proj, tid, maker=maker, checker=FakeChecker(reviews=[Review("fail", [scope], "nothing")])) == "disputed"
    dec = decide.decision(proj, tid)
    assert dec.kind == "conflict" and dec.recommend == "intent" and lints(proj, show.report(proj, tid))
    decide.apply(proj, tid, answer)
    assert SPAWNED[-1][-1] == then
    if answer == "intent":
        assert kinds(proj, "task.redraft")[0]["reason"].startswith("the intent wins over the plan")


def test_every_kind_of_decision_is_one_lint_clean_question(proj):
    tid = pilot.intake(proj, WANT)["task"]
    pilot_run(proj, tid)
    cases = [
        ("disagreement.raised", {"stage": "scope"}, "extra.py changed but isn't in the plan's files", "scope", "reject"),
        ("disagreement.raised", {"stage": "check"}, "the check still fails after 3 rework cycles: tests", "rework", "reject"),
        ("disagreement.raised", {"stage": "check"}, "checker error: garbled reply", "checker", "retry"),
        ("disagreement.raised", {"stage": "check"}, "the plan's tests couldn't run (exit 4): no module", "tests", "retry"),
        ("disagreement.raised", {"stage": "guard"}, "diff touches protected files: CLAUDE.md", "guard", "reject"),
        ("stuck.raised", {}, "drafting still failed after 2 redrafts: the plan lists setup.py", "drafting", "reject"),
        ("stuck.raised", {}, "the same call was refused 3 times: shell.run curl", "stuck", "retry"),
    ]
    for kind, data, why, expect, recommend in cases:
        item = proj.ledger.append(kind, "parallax", why, task=tid, **data)
        dec = decide.decision(proj, tid)
        assert (dec.kind, dec.recommend) == (expect, recommend), why
        assert dec.question.endswith("?") and dec.recommend in [o.name for o in dec.options]
        card = show.report(proj, tid)
        assert card.startswith("Type: Decision needed\nBottom line: Needs you: ") and "\nDecisions\n- Decide: " in card
        assert lints(proj, card), card
        proj.resolve(item["id"], False, "next case")


def test_options_and_reasons_are_checked(proj):
    tid = pilot.intake(proj, WANT)["task"]
    proj.ledger.append("disagreement.raised", "parallax", "extra.py changed but isn't in the plan's files", task=tid,
                       stage="scope")
    with pytest.raises(ParallaxError, match="choose from: accept, reject, drop"):
        decide.apply(proj, tid, "launch")
    with pytest.raises(ParallaxError, match="accept needs a reason"):
        decide.apply(proj, tid, "accept")
    assert decide.apply(proj, tid, "drop", "not needed") == f"dropped {tid}. it's out of the inbox."
    assert proj.task(tid)["status"] == "rejected" and inbox.items(proj) == []


def test_a_redrafted_card_leads_with_what_changed_since_your_reject(proj):
    """29f523: the redraft came back, and nothing on its card said it was one."""
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid, maker=ScriptedAgent(steps=[("write", "README.md", "one\n")])) == "ready"
    pilot.redraft(proj, tid, "say which shell each step runs in")
    changed = {**docs(), "intent": docs()["intent"].replace("Keep the macOS steps.", "Keep the macOS steps. Name the shell.")}
    maker = ScriptedAgent(steps=[("write", "README.md", "two\n")])
    assert pilot_run(proj, tid, FakeDrafter(changed), maker) == "ready"
    text = show.report(proj, tid)
    assert lints(proj, text)
    head, body = text.split("\nChanged since last time\n", 1)
    assert "Bottom line: Ready again after your reject:" in head
    lead = [l[2:] for l in body.splitlines() if l.startswith("- ")][:4]
    assert lead[0].startswith('you rejected the last version: "say which shell each step runs in"')
    assert lead[1].startswith("intent: Constraints changed (+1 -1 lines); header, Problem, Outcome the same")
    assert lead[2].startswith("plan: unchanged")
    assert lead[3].startswith("the change: differs from the rejected one in README.md")
    assert text.index("Changed since last time") < text.index("\nFound\n")  # first, before the evidence
    from parallax import views
    card = views.card(proj, tid)
    assert card["redraft"] and card["changed"][0]["text"].startswith("you rejected the last version")


def test_a_first_attempt_card_says_nothing_about_redrafts(proj):
    tid = pilot.intake(proj, WANT)["task"]
    assert pilot_run(proj, tid) == "ready"
    text = show.report(proj, tid)
    assert "again after your reject" not in text and "you rejected" not in text
