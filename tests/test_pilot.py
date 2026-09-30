from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import approvals, build, inbox, lifecycle, lint, pilot, planfit, preflight, show, stats
from parallax.accept import accept
from parallax.cli import main
from parallax.core import POLICY_FILE, Project
from test_lifecycle_gates import WANT, docs, make_key

EF4163_PLAN = """Bottom line: Fixes all five WSL install problems in README.md and adds a test that pins the doctor sample.

Not looked at: whether a fresh WSL2 distro reproduces the ownership error.

## Steps
1. Edit README.md.
"""


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


def humans(proj, tid):
    """What you did for a task, besides describing it."""
    return [e for e in proj.ledger.entries()
            if e["data"].get("task") == tid and e["actor"] == "human" and e["kind"] != "task.created"]


def run_pilot(proj, tid, drafter, maker=None, checker=None, runner=None):
    maker = maker or ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    return build.run_mode(proj, tid, "pilot", drafter, lambda left, settings: maker, checker or FakeChecker(),
                          test_runner=runner or junit_runner(), preflight_runner=good_probe)


# parallax do: from plain words to one inbox item ---------------------------------------------------

def test_do_answers_at_once_and_the_pilot_needs_nobody(proj, monkeypatch, capsys):
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append((argv, env)) or 321)
    monkeypatch.chdir(proj.root)
    assert main(["do", WANT]) == 0
    [tid] = proj.tasks()
    assert capsys.readouterr().out == (f"task {tid}: on it. drafting, building and checking run without you.\n"
                                       "it comes to parallax inbox when it needs you.\n")
    [(argv, env)] = spawned
    assert argv[-3:] == [str(proj.root), tid, "pilot"] and env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] == "1"
    assert kinds(proj, "build.started")[0]["data"]["mode"] == "pilot" and proj.task(tid)["status"] == "drafting"

    assert run_pilot(proj, tid, FakeDrafter(docs())) == "ready"
    assert humans(proj, tid) == []  # no intent, plan or launch approval from you
    [g] = kinds(proj, "gate.approved")
    assert g["actor"] == "parallax" and g["data"]["rule"].startswith("launch rule: a small task, cap $2.00")
    assert approvals.valid(approvals.load_key(), g["data"])  # signed, like yours
    assert inbox.items(proj) == [{"task": tid, "state": "ready", "title": "fixing the README install steps"}]


def test_inbox_shows_one_item_per_task_and_what_works_without_you(proj, monkeypatch, capsys):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    ready = pilot.intake(proj, WANT)["task"]
    run_pilot(proj, ready, FakeDrafter(docs()))
    busy = pilot.intake(proj, "another thing")["task"]
    monkeypatch.chdir(proj.root)
    main(["inbox"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith(f"{ready}  ready") and "fixing the README install steps" in out[0]
    assert out[1:] == ["parallax show <task> for its card.", "1 working without you."]
    assert busy not in "\n".join(out[:1])


# drafts Parallax fixes itself -----------------------------------------------------------------------

def test_normalize_repairs_ef4163s_split_header():
    assert lint.lint_lifecycle(EF4163_PLAN, "spec")  # the exact slip: a blank line inside the header
    fixed = lint.normalize(EF4163_PLAN, "spec")
    assert lint.lint_lifecycle(fixed, "spec") == []
    assert fixed.splitlines()[:3] == [EF4163_PLAN.splitlines()[0], EF4163_PLAN.splitlines()[2], ""]
    assert lint.normalize("```markdown\n" + EF4163_PLAN + "```", "spec") == fixed
    assert "\u2014" not in lint.normalize(EF4163_PLAN.replace("Edit README.md.", "Edit README.md \u2014 all of it."), "spec")


def test_a_draft_that_fails_lint_goes_back_to_the_drafter_not_to_you(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    broken = docs()["plan"].replace("Not looked at: nothing\n", "")
    drafter = FakeDrafter({**docs(), "plan": [broken, docs()["plan"]]})
    assert run_pilot(proj, tid, drafter) == "ready"
    assert "expected 'Not looked at:'" in drafter.requests[-1]  # the exact problem went back
    assert humans(proj, tid) == []


# the plan against the intent, by code --------------------------------------------------------------

def test_planfit_catches_what_cost_rejections_on_e9a55a():
    intent = docs()["intent"].replace("scope: README.md, tests/**, Makefile, extra.py, pytest.ini, tox.ini",
                                      "scope: README.md") + "budget: 4.00\n"
    intent = intent.replace("title: fixing", "budget: 4.00\ntitle: fixing")
    plan = lint.plan_block(docs()["plan"])[0]
    budget = {"small_cap_usd": 5.0, "large_cap_usd": 20.0}
    found = " | ".join(planfit.problems(intent, plan, 0.5, budget))
    assert "the plan lists tests/test_readme.py, which the intent's scope (README.md) doesn't allow" in found
    assert "the intent names a budget of $4.00, but the plan's cap is $2.00" in found
    assert planfit.problems(docs()["intent"], {**plan, "covers": {}}, 0.2, budget) == [
        "the plan doesn't cover outcome 1; add it to covers with the tests or steps that prove it"]
    assert "isn't above what drafting already spent" in " ".join(planfit.problems(docs()["intent"], plan, 2.0, budget))
    assert "over the policy's $1.00" in " ".join(planfit.problems(docs()["intent"], plan, 0.1, {**budget, "small_cap_usd": 1.0}))
    assert planfit.problems(docs()["intent"], plan, 0.2, budget) == []  # $0.20 drafting, twice $0.90: $2.00


def test_a_misfit_is_redrafted_on_its_own_then_comes_to_you_after_two_tries(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    outside = docs()["plan"].replace('"README.md", "tests/test_readme.py"', '"README.md", "setup.py"')
    good = FakeDrafter({**docs(), "plan": [outside, docs()["plan"]]})
    tid = pilot.intake(proj, WANT)["task"]
    assert run_pilot(proj, tid, good) == "ready"
    assert "setup.py, which the intent's scope" in good.requests[-1]
    assert len(kinds(proj, "draft.misfit")) == 1

    never = FakeDrafter({**docs(), "plan": outside})
    tid = pilot.intake(proj, "again")["task"]
    assert run_pilot(proj, tid, never) == "stuck"
    assert [r.split("\n", 1)[0] for r in never.requests].count(f"Draft docs/tasks/{tid}/plan.md in exactly this shape:") == 3
    [item] = [e for e in proj.inbox() if e["data"]["task"] == tid]
    assert item["reason"].startswith("drafting still failed after 2 redrafts: the plan lists setup.py")
    assert inbox.items(proj)[-1]["state"] == "needs you"


# the launch rule --------------------------------------------------------------------------------------

@pytest.mark.parametrize("policy,why", [
    ("[launch]\nreview_paths = [\"README.md\"]\n", "the plan touches README.md, which is in review_paths"),
    ("[launch]\nauto_launch_usd = 1.0\n", "the budget cap ($2.00) is over auto_launch_usd ($1.00)"),
    ("[launch]\nreview_plans = true\n", "review_plans is on in the policy, so every plan waits for you"),
])
def test_outside_the_launch_rule_the_plan_waits_for_you(repo, monkeypatch, capsys, policy, why):
    (repo / POLICY_FILE).write_text(policy)
    make_key()
    proj = Project.init(repo)
    monkeypatch.setattr(build, "_spawn", lambda *a: 7)
    tid = pilot.intake(proj, WANT)["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    assert run_pilot(proj, tid, FakeDrafter(docs()), maker) == "needs you"
    assert maker.goals == [] and kinds(proj, "gate.approved") == []
    assert kinds(proj, "review.requested")[0]["reason"] == why
    card = show.report(proj, tid)
    assert card.startswith("Type: Decision needed\nBottom line: The plan waits for you before it runs.")
    assert f"why it waits: {why}" in card and "cost: estimated $0.90, cap $2.00" in card

    monkeypatch.setattr(preflight, "run_srt", good_probe)
    monkeypatch.chdir(proj.root)
    assert main(["approve", tid]) == 0  # your one touch starts it
    out = capsys.readouterr().out
    assert f"building {tid} without you" in out and kinds(proj, "build.started")[-1]["data"]["mode"] == "build"


def test_a_large_task_gets_its_intent_approved_by_code_and_its_plan_waits(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    drafter = FakeDrafter(docs(size="large"))
    assert run_pilot(proj, tid, drafter) == "needs you"
    [g] = kinds(proj, "gate.approved")
    assert g["actor"] == "parallax" and g["data"]["gate"] == "intent"
    assert "size: large" in kinds(proj, "review.requested")[0]["reason"]


# the em dash, by code --------------------------------------------------------------------------------

def test_an_added_em_dash_is_reworked_before_the_checker_sees_it(proj, monkeypatch):
    """Parallax's own style rule, in a repo whose policy turns it on: Parallax's own."""
    (proj.root / POLICY_FILE).write_text("[check]\nno_em_dashes = true\n")
    proj.reload_policy()
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "one \u2014 two\n")])
    checker = FakeChecker()

    def fix(cwd):
        (cwd / "README.md").write_text("one, two\n")

    original = maker.run

    def run(goal, cwd, fn, stage="build", env=None):
        if "an em dash was added" in goal:
            maker.steps["build"] = [("call", fix)]
        return original(goal, cwd, fn, stage, env)

    maker.run = run
    assert run_pilot(proj, tid, FakeDrafter(docs()), maker, checker) == "ready"
    [found] = kinds(proj, "check.found")
    assert found["data"]["findings"] == ["blocker README.md:1: an em dash was added; use a comma or a colon"]
    assert len(checker.briefs) == 1 and "\u2014" not in checker.briefs[0]


def test_in_any_other_repo_an_em_dash_is_fine(proj, monkeypatch):
    """The default policy, as parallax init gives any repo: no rework, and Second Eye sees the dash."""
    assert proj.policy.check["no_em_dashes"] is False
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    checker = FakeChecker()
    assert run_pilot(proj, tid, FakeDrafter(docs()), ScriptedAgent(steps=[("write", "README.md", "one \u2014 two\n")]),
                     checker) == "ready"
    assert not kinds(proj, "check.found") and not kinds(proj, "rework.started")
    assert len(checker.briefs) == 1 and "+one \u2014 two" in checker.briefs[0]


# your own output never fails your own lint --------------------------------------------------------------

@pytest.mark.parametrize("title", ["x", "a very " * 60 + "long title", "one. two. three."])
def test_reports_always_lint_even_with_long_or_odd_text(proj, monkeypatch, capsys, title):
    long = docs()
    long["intent"] = long["intent"].replace("title: fixing the README install steps", f"title: {title}")
    long["plan"] = long["plan"].replace("Not looked at: nothing", "Not looked at: " + "unchecked " * 120)
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    run_pilot(proj, tid, FakeDrafter(long))
    monkeypatch.chdir(proj.root)
    assert main(["show", tid]) == 0
    out = capsys.readouterr().out
    assert "failed lint" not in out
    ids = {e["id"] for e in proj.ledger.entries()}
    assert lint.lint_report(out, root=proj.root, ledger_ids=ids) == []


def test_fit_repairs_and_the_repair_is_recorded(proj, monkeypatch, capsys):
    text = "Type: FYI\nBottom line: " + "word " * 50 + ".\nNot looked at: nothing\nNext: nobody.\nFound\n- no source"
    fitted, repairs = lint.fit(text)
    assert lint.lint_report(fitted) == [] and "shortened the header" in repairs
    assert fitted.endswith("- no source (Unverified)")


# measuring it ----------------------------------------------------------------------------------------------

def test_stats_counts_one_touch_for_a_hands_free_task(proj, monkeypatch, capsys):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    free = pilot.intake(proj, WANT)["task"]
    run_pilot(proj, free, FakeDrafter(docs()))
    accept(proj, free)

    fussy = lifecycle.new_intent(proj, "the old way", FakeDrafter(docs()))["task"]
    plan = lifecycle.doc_path(proj, fussy, "plan")
    lifecycle.reject(proj, fussy, "too narrow")
    plan.write_text(plan.read_text().replace("1. Edit README.md.", "1. Edit README.md by hand."))
    lifecycle.approve(proj, fussy)  # a reject, a hand edit and an approve: 3 touches
    proj.ledger.append("task.rejected", "human", "not needed after all", task=fussy)  # and a 4th, which ends it

    assert stats.touches(proj) == {free: 1, fussy: 4}
    monkeypatch.chdir(proj.root)
    main(["stats"])
    out = capsys.readouterr().out.splitlines()
    assert out[0].split() == ["task", "touches", "cost", "status"]
    assert out[-1] == "average 2.5 touches per finished task (2 finished), target 1. 1 took more."
    pilot.intake(proj, "still going")  # an unfinished task stays out of the average
    main(["stats"])
    assert capsys.readouterr().out.splitlines()[-1].startswith("average 2.5 touches per finished task (2 finished)")


def test_diff_shows_the_reviewed_change(proj, monkeypatch, capsys):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    run_pilot(proj, tid, FakeDrafter(docs()))
    (Path(proj.task(tid)["worktree"]) / "later.txt").write_text("not reviewed\n")
    monkeypatch.chdir(proj.root)
    main(["diff", tid])
    out = capsys.readouterr().out
    assert "+ok" in out and "later.txt" not in out


def test_drafters_leftovers_are_cleaned_so_setup_still_runs(repo, monkeypatch):
    """Live in M12: the drafters' sessions left the sandbox's placeholders, and setup refused a 'changed' worktree."""
    (repo / POLICY_FILE).write_text('[build]\nsetup = "mkdir -p \\"$PARALLAX_VENV/bin\\""\n')
    make_key()
    proj = Project.init(repo)
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])
    litter = FakeDrafter(docs(), reads=[])
    original = litter.run
    litter.run = lambda goal, cwd, fn, stage="build", env=None: ((Path(cwd) / ".env").touch(), original(goal, cwd, fn, stage, env))[1]
    assert run_pilot(proj, tid, litter) == "ready"
    assert not (wt / ".env").exists() and kinds(proj, "setup.ran")


def test_an_error_in_the_background_comes_to_you(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    tid = pilot.intake(proj, WANT)["task"]

    def boom(*a):
        raise RuntimeError("the adapter fell over")

    monkeypatch.setattr(pilot, "draft_until_fit", boom)
    assert build.run_mode(proj, tid, "pilot", None, None, None) == "stuck"
    assert kinds(proj, "stuck.raised")[0]["data"]["error"] is True
    [item] = proj.inbox()
    assert "the adapter fell over" in item["reason"] and inbox.items(proj)[0]["state"] == "needs you"


def test_the_cap_leaves_room_for_one_rework(proj):
    """ee8178: a $0.60 cap for work estimated just under it; one rework spent it."""
    plan = {**lint.plan_block(docs()["plan"])[0], "estimated_cost_usd": 0.55, "budget_cap_usd": 0.6}
    found = planfit.problems(docs()["intent"], plan, 0.22, proj.policy.budget)
    assert found == ["the cap ($0.60) leaves no room for a rework round: make it at least $1.32 "
                     "(drafting so far, plus twice the estimate)"]
    assert planfit.problems(docs()["intent"], {**plan, "budget_cap_usd": 1.32}, 0.22, proj.policy.budget) == []
    assert "Field" in planfit.problems(docs()["intent"], {**plan, "budget_cap_usd": 1.32}, 0.22,
                                           proj.policy.budget, reserve=1.5)[0]


def test_a_short_cap_is_raised_by_code_not_redrafted(proj, monkeypatch):
    """a56043: drafting cost more than the whole estimate, and the build ran out of cap."""
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    short = docs()["plan"].replace("estimated_cost_usd = 0.9", "estimated_cost_usd = 1.1")
    tid = pilot.intake(proj, WANT)["task"]
    drafter = FakeDrafter({**docs(), "plan": short}, cost=0.6)
    assert pilot.draft_until_fit(proj, tid, drafter) == "fit"
    plan = lifecycle.plan_data(proj, tid)
    assert plan["budget_cap_usd"] == 3.4  # $1.20 of drafting, plus twice $1.10
    raised = [e for e in kinds(proj, "draft.recorded") if e["actor"] == "parallax"]
    assert "raised the cap from $2.00 to $3.40" in raised[-1]["reason"]
    assert not kinds(proj, "draft.misfit") and stats.touches(proj)[tid] == 0  # no redraft, and not a hand edit


def test_a_rejected_task_leaves_the_inbox(proj, monkeypatch, capsys):
    """ef4163: rejected at its gate, and still shown as needs you."""
    old = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    lifecycle.reject(proj, old, "superseded")  # how ef4163 was rejected, before M13
    assert inbox.items(proj) == []
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    assert inbox.items(proj)[0]["task"] == tid
    monkeypatch.chdir(proj.root)
    main(["reject", tid, "--drop", "--reason", "superseded"])
    capsys.readouterr()
    assert inbox.items(proj) == []
    main(["task", "list"])
    out = capsys.readouterr().out
    assert f"{tid}  [rejected]" in out and f"{old}  [rejected]" in out

def test_the_background_process_runs_the_installed_parallax_never_the_repos(proj, monkeypatch):
    """2da12f: in a repo with its own parallax/ package, `python -m` from the repo's folder ran that copy."""
    seen = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: seen.append(argv) or 9)
    pilot.intake(proj, WANT)
    assert seen[0][1:4] == ["-P", "-u", "-m"]  # -P: the working folder never goes on sys.path



def test_the_drafter_model_is_a_setting(repo, monkeypatch):
    make_key()
    proj = Project.init(repo)
    assert proj.policy.draft == {"model": "claude-sonnet-5-5"}  # the experiment's winner
    (repo / POLICY_FILE).write_text('[draft]\nmodel = "claude-opus-5"\n')
    assert Project(repo).policy.draft["model"] == "claude-opus-5"
    from parallax.policy import Policy
    with pytest.raises(ValueError, match="unknown"):
        Policy({}, draft={"together": True})
    seen = []
    monkeypatch.setattr("parallax.agents.claude.ClaudeAgent", lambda **kw: seen.append(kw) or kw, raising=False)
    build._drafter(1.0, "claude-sonnet-5-5")
    assert seen[0]["model"] == "claude-sonnet-5-5"

def test_a_cap_is_never_raised_past_the_size_limit_and_the_drafter_hears_what_fits(proj, monkeypatch):
    """42b54a: code raised the cap to $5.60, over the $5 small-task limit, and three redrafts couldn't fix it."""
    monkeypatch.setattr(build, "_spawn", lambda *a: 9)
    big = docs()["plan"].replace("estimated_cost_usd = 0.9", "estimated_cost_usd = 2.6").replace(
        "budget_cap_usd = 2.0", "budget_cap_usd = 3.0")
    tid = pilot.intake(proj, WANT)["task"]
    pilot.draft_until_fit(proj, tid, FakeDrafter({**docs(), "plan": big}))
    assert lifecycle.plan_data(proj, tid)["budget_cap_usd"] == 3.0  # left as drafted: no raise past $5
    misfit = kinds(proj, "draft.misfit")[0]["reason"]
    assert "the estimate ($2.60) is too big for a small task" in misfit and "estimate at most $2.40" in misfit


@pytest.mark.parametrize("field,value", [("domains", "pypi.org"), ("outside_reads", "/opt/data")])
def test_a_plan_that_crosses_the_boundary_never_launches_by_the_rule(proj, monkeypatch, field, value):
    """A drafter steered by repo text can list a domain or an outside read; only you can approve that."""
    monkeypatch.setattr(build, "_spawn", lambda *a: 7)
    plan = docs()["plan"].replace(f"{field} = []", f'{field} = ["{value}"]')
    tid = pilot.intake(proj, WANT)["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    assert run_pilot(proj, tid, FakeDrafter({**docs(), "plan": plan}), maker) == "needs you"
    assert maker.goals == [] and kinds(proj, "gate.approved") == []
    assert kinds(proj, "review.requested")[0]["reason"] == \
        f"the plan's {field} ({value}) cross the boundary, so only you can approve it"


# the turn cap, empty files, and the two lines under every question ------------------------------------

def test_the_makers_turn_cap_comes_to_you_the_way_a_cap_hit_does(proj, monkeypatch):
    from parallax import decide
    from parallax.agents.base import AgentResult

    class OutOfTurns:
        def run(self, goal, cwd, fn, stage="build", env=None):
            return AgentResult("error", "stopped at the turn cap (150 turns)", 0.4)
    monkeypatch.setattr(build, "_spawn", lambda *a: 7)
    tid = pilot.intake(proj, WANT)["task"]
    assert build.run_mode(proj, tid, "pilot", FakeDrafter(docs()), lambda left, settings: OutOfTurns(), FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=good_probe) == "stuck"
    [item] = proj.inbox()
    assert item["kind"] == "stuck.raised" and item["data"]["turns"] is True and item["reason"] == "stopped at the turn cap (150 turns)"
    dec = decide.decision(proj, tid)
    assert dec.kind == "turns" and dec.recommend == "retry" and [o.name for o in dec.options] == ["retry", "reject", "drop"]
    assert proj.policy.limits["maker_turns"] == 150
    card = show.report(proj, tid)
    assert "Whose call: you, as the engineer." in card and "Why a human: Maker used every turn" in card
    assert lint.lint_report(card, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []


def test_the_sdk_result_maps_to_the_turn_cap():
    from parallax.agents.claude import outcome
    assert outcome("error_max_turns", True, "", 0.1, 2.0, 150).summary == "stopped at the turn cap (150 turns)"
    assert outcome("error_max_budget_usd", True, "", 0.1, 2.0, 150).summary == "stopped at the budget cap ($2.0)"
    assert outcome("success", False, "conflict: the plan says otherwise", 0.1, 2.0, 150).status == "conflict"
    assert outcome("success", False, "Done.", 0.1, 2.0, 150).status == "done"


def test_empty_files_outside_the_plan_are_removed_by_code_and_a_full_one_comes_to_you(proj, monkeypatch):
    monkeypatch.setattr(build, "_spawn", lambda *a: 7)
    tid = pilot.intake(proj, WANT)["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n"), ("write", "notes.txt", "")])
    assert run_pilot(proj, tid, FakeDrafter(docs()), maker) == "ready"
    cleaned = [e for e in kinds(proj, "sandbox.cleaned") if e["data"]["files"] == ["notes.txt"]]
    assert cleaned and cleaned[0]["reason"] == "removed empty files outside the plan's files"
    assert not (Path(proj.task(tid)["worktree"]) / "notes.txt").exists() and not proj.inbox()

    tid = pilot.intake(proj, "again")["task"]
    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n"), ("write", "notes.txt", "real work\n")])
    assert run_pilot(proj, tid, FakeDrafter(docs()), maker) == "disputed"
    [item] = [e for e in proj.inbox() if e["data"]["task"] == tid]
    assert item["data"]["stage"] == "scope" and "notes.txt" in item["reason"]


def test_every_needs_you_card_says_whose_call_and_why(proj, monkeypatch):
    """Two lines under the question, set by code from the kind: security for a secrets or protected-path item."""
    from parallax import decide, views
    monkeypatch.setattr(build, "_spawn", lambda *a: 7)
    tid = pilot.intake(proj, WANT)["task"]
    assert run_pilot(proj, tid, FakeDrafter(docs())) == "ready"
    cases = [
        ("disagreement.raised", {"stage": "guard"}, "diff touches protected files: CLAUDE.md", "security",
         "a protected path was touched, and no rule lets code accept that"),
        ("disagreement.raised", {"stage": "scope", "files": [{"path": ".env", "cause": "outside", "size": 9, "secret": True}]},
         ".env looks like a secrets file and has content", "security",
         "a file that looks like a secret has content, and no rule lets code accept that"),
        ("disagreement.raised", {"stage": "scope"}, "extra.py changed but isn't in the plan's files", "you, as the engineer",
         "the change reached outside the plan you approved"),
        ("stuck.raised", {"budget": True}, "the budget cap ran out", "you, as the engineer",
         "the cap you approved is spent, and raising it is spending more"),
    ]
    for kind, data, why, owner, reason in cases:
        item = proj.ledger.append(kind, "parallax", why, task=tid, **data)
        dec = decide.decision(proj, tid)
        assert (dec.owner, dec.why_human) == (owner, reason), why
        card = show.report(proj, tid)
        assert f"- Whose call: {owner}.\n- Why a human: {reason}." in card
        assert lint.lint_report(card, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []
        actions = views.card(proj, tid)["actions"]
        assert (actions["owner"], actions["why_human"]) == (owner, reason)
        proj.resolve(item["id"], False, "next case")
    assert set(decide.WHY_HUMAN) >= {"launch", "review", "cap", "conflict", "scope", "flows", "rework", "checker",
                                     "tests", "guard", "drafting", "error", "stuck", "turns"}
