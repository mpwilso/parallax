"""M15, the light conductor: one request, split by Focus into parts that are tasks of their own.

No test calls a model: Focus, Maker and Second Eye are the fakes, and every background process is a
recorded spawn, run here in turn."""
import os
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner
from parallax import build, conductor, decide, inbox, lint, pilot, preflight, show, split, stats, views
from parallax.accept import accept
from parallax.cleanup import dropped
from parallax.cli import main
from parallax.core import POLICY_FILE, Project
from parallax.policy import Policy
from test_lifecycle_gates import docs, make_key

CONDUCTOR = "[conductor]\nmax_parts = 4\nmax_parallel = 2\n"
ROOMY = CONDUCTOR + "[launch]\nauto_launch_usd = 10.0\n"  # two parts' caps fit under it together
WORK = "fix the README typo, and fix the notes typo"


def part_docs(name, test, title):
    """docs() for one part: its own file, test and title, its scope just those two."""
    d = docs()
    d["intent"] = d["intent"].replace("scope: README.md, tests/**, Makefile, extra.py, pytest.ini, tox.ini",
                                      f"scope: {name}, {test}").replace("fixing the README install steps", title)
    d["plan"] = d["plan"].replace("tests/test_readme.py", test).replace('"README.md"', f'"{name}"')
    return d


README = part_docs("README.md", "tests/test_readme.py", "fixing the README typo")
NOTES = part_docs("NOTES.md", "tests/test_notes.py", "fixing the notes typo")
TODO = part_docs("TODO.md", "tests/test_todo.py", "fixing the todo typo")
MORE = part_docs("README.md", "tests/test_more.py", "adding a README section")
PARTS = {"fix the README typo": README, "fix the notes typo": NOTES, "fix the todo typo": TODO,
         "add a README section": MORE}


def split_text(*parts):
    lines = ["Bottom line: The request is separate changes, one part each.", "Not looked at: nothing", "", "## Parts"]
    for n, (goal, scope) in enumerate(parts, 1):
        lines += [f"{n}. goal: {goal}", f"   scope: {scope}"]
    return "\n".join(lines) + "\n"


A = ("fix the README typo", "README.md, tests/test_readme.py")
B = ("fix the notes typo", "NOTES.md, tests/test_notes.py")
C = ("fix the todo typo", "TODO.md, tests/test_todo.py")
TWO = split_text(A, B)
IN_TURN = split_text(A, ("add a README section", "README.md, tests/test_more.py"))


class Focus(FakeDrafter):
    """Focus for a split request: the split, then each part's intent and plan, picked by the part the
    request names. A request that names no part gets the whole request's docs."""

    def __init__(self, split_doc, parts=PARTS, whole=None, **kw):
        super().__init__({"split": split_doc}, **kw)
        self.parts, self.whole = parts, whole or docs()

    def run(self, goal, cwd, permission_fn, stage="build", env=None):
        if "/split.md in exactly this shape" not in goal.split("\n", 1)[0]:
            mine = next((d for g, d in self.parts.items() if f"Draft for this part only: {g}\n" in goal), self.whole)
            self.docs.update(intent=mine["intent"], plan=mine["plan"])
        return super().run(goal, cwd, permission_fn, stage, env)


def run_pilot(proj, tid, drafter, writes="README.md", text="fixed\n"):
    maker = ScriptedAgent(steps=[("write", writes, text)])
    return build.run_mode(proj, tid, "pilot", drafter, lambda left, settings: maker, FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=good_probe)


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


def at(proj, kind, task):
    return next(i for i, e in enumerate(proj.ledger.entries()) if e["kind"] == kind and e["data"].get("task") == task)


@pytest.fixture
def started(monkeypatch):
    """Every background pilot or build started: (task, mode). A live pid, as a real one would be."""
    spawned = []
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: spawned.append((argv[-2], argv[-1])) or os.getpid())
    monkeypatch.setattr(preflight, "run_srt", good_probe)
    return spawned


def project(repo, policy=ROOMY):
    (repo / POLICY_FILE).write_text(policy)
    make_key()
    return Project.init(repo)


def linted(proj, card):
    """The card passes the output shape, its citations checked against this ledger."""
    return lint.lint_report(card, root=proj.root, ledger_ids={e["id"] for e in proj.ledger.entries()}) == []


def other(proj, *known):
    return next(t for t in proj.tasks() if t not in known)


# the policy ---------------------------------------------------------------------------------------

def test_without_the_conductor_table_every_request_is_one_task(repo, started):
    assert Policy.from_dict({}).conductor == {"max_parts": 1, "max_parallel": 2}
    assert Policy.from_dict({"conductor": {}}).conductor == {"max_parts": 4, "max_parallel": 2}
    for bad in ({"max_parts": 0}, {"max_parallel": True}, {"parts": 2}):
        with pytest.raises(ValueError, match=r"\[conductor\]"):
            Policy.from_dict({"conductor": bad})
    proj = project(repo, "[launch]\nauto_launch_usd = 3.0\n")
    tid = pilot.intake(proj, WORK)["task"]
    drafter = FakeDrafter(docs())
    assert run_pilot(proj, tid, drafter) == "ready"
    assert "request" not in proj.task(tid) and kinds(proj, "request.split") == []
    assert drafter.requests[0].startswith(f"Draft docs/tasks/{tid}/intent.md")  # no split drafted


# the split, checked by code -----------------------------------------------------------------------

def test_the_split_is_read_and_checked_by_code():
    text = "Bottom line: Two parts.\nNot looked at: nothing\n\n## Parts\n1. **goal:** fix it; scope: a.md, `b.md`\n2. goal: other\n"
    assert lint.split_parts(text) == [{"n": 1, "goal": "fix it", "scope": ["a.md", "b.md"]}, {"n": 2, "goal": "other", "scope": None}]
    assert lint.lint_lifecycle(text, "split") == [(1, "part 2 needs a 'scope:' line")]
    assert lint.lint_lifecycle("Bottom line: One.\nNot looked at: nothing\n", "split")[0][1].startswith("split needs a '## Parts'")
    assert split.overlaps(["parallax/*.py"], ["parallax/web/*"]) and split.overlaps(["docs/"], ["docs/using.md"])
    assert not split.overlaps(["README.md"], ["NOTES.md"]) and not split.overlaps(["parallax/*.py"], ["parallax/web/app.js"])
    assert all(split.protected(p) for p in ("CLAUDE.md", "docs/tasks/**", ".claude/*", "parallax.policy.toml", "docs/parallax.md"))
    assert not split.protected("docs/using.md")
    parts = [{"n": 1, "goal": "a", "scope": ["README.md"]}, {"n": 2, "goal": "b", "scope": ["NOTES.md"]},
             {"n": 3, "goal": "c", "scope": ["README.md", "NOTES.md"]}, {"n": 4, "goal": "d", "scope": ["TODO.md"]}]
    assert split.order(parts) == {1: None, 2: 1, 3: 2, 4: None}  # 3 needs both, so 1, 2 and 3 run in turn
    assert split.problems(parts, 4) == []
    assert split.problems([{"n": 1, "goal": " ", "scope": []}, {"n": 2, "goal": "x", "scope": ["../up", ".mcp.json"]}], 1) == [
        "the split has 2 parts, and the policy allows at most 1 ([conductor] max_parts): join parts, or make it one part",
        "part 1 has no goal: say what it does, in plain words",
        "part 1 has no scope: list the paths or globs it may touch, tests included",
        "part 2's scope names ../up, which isn't a path inside the repo",
        "part 2's scope names .mcp.json, which is protected: no part may ever write it"]


# one part ------------------------------------------------------------------------------------------

def test_a_one_part_request_is_one_task_as_today(repo, started):
    proj = project(repo, CONDUCTOR)
    tid = pilot.intake(proj, "the README install steps are wrong for WSL")["task"]
    drafter = FakeDrafter(docs())  # its split: one part (fakes.ONE_PART)
    assert run_pilot(proj, tid, drafter) == "ready"
    assert drafter.requests[0].startswith(f"Draft docs/tasks/{tid}/split.md in exactly this shape")
    [s] = kinds(proj, "request.split")
    assert s["data"]["request"] == tid and len(s["data"]["parts"]) == 1
    assert list(proj.tasks()) == [tid] and kinds(proj, "launch.held") == [] and started == [(tid, "pilot")]
    [g] = kinds(proj, "gate.approved")
    assert g["actor"] == "parallax" and g["reason"] == "launch rule: a small task, cap $2.20 within auto_launch_usd $3.00, nothing in review_paths"
    assert inbox.items(proj) == [{"task": tid, "state": "ready", "title": "fixing the README install steps"}]
    assert conductor.groups(proj) == [] and not any(line.startswith("request") for line in stats.report(proj))


# two parts side by side -----------------------------------------------------------------------------

def test_two_parts_that_dont_overlap_build_side_by_side_and_come_back_as_two_ready_cards(repo, started, monkeypatch, capsys):
    proj = project(repo)
    lead = pilot.intake(proj, WORK)["task"]
    focus = Focus(TWO)
    assert run_pilot(proj, lead, focus, "README.md") == "held"  # its plan fits: it waits for the other part's
    second = other(proj, lead)
    [s] = kinds(proj, "request.split")
    assert s["data"]["request"] == lead and s["data"]["sha"]
    assert [(p["n"], p["goal"], p["scope"], p["after"]) for p in s["data"]["parts"]] == [
        (1, "fix the README typo", ["README.md", "tests/test_readme.py"], None),
        (2, "fix the notes typo", ["NOTES.md", "tests/test_notes.py"], None)]
    assert proj.task(lead)["request"] == lead  # each task.created carries the request
    assert (proj.task(second)["request"], proj.task(second)["part"], proj.task(second)["goal"]) == (lead, 2, "fix the notes typo")
    assert started == [(lead, "pilot"), (second, "pilot")]  # the second part drafted while the first did
    assert proj.task(lead)["status"] == "planned" and kinds(proj, "gate.approved") == []
    intent = focus.requests[1]  # the lead's intent: the whole request as typed, and only its own part to draft
    assert WORK in intent and "Draft for this part only: fix the README typo\n" in intent and "part 2, fix the notes typo" in intent

    assert run_pilot(proj, second, focus, "NOTES.md") == "ready"  # the last plan in launches both
    approved = {g["data"]["task"]: g for g in kinds(proj, "gate.approved")}
    assert set(approved) == {lead, second} and {g["actor"] for g in approved.values()} == {"parallax"}
    assert approved[lead]["reason"] == ("launch rule: a small task, cap $2.30 within auto_launch_usd $10.00, nothing in "
                                        "review_paths; this request's 2 parts together cap $4.50, within auto_launch_usd $10.00")
    assert kinds(proj, "review.requested") == []  # under auto_launch_usd together: no question
    assert started[-1] == (lead, "pilot")  # its pilot again, to build the approved plan
    assert at(proj, "gate.approved", lead) < at(proj, "maker.started", second)  # both launched before either built

    assert run_pilot(proj, lead, focus, "README.md") == "ready"
    assert "NOTES.md" in proj.diff(second) and "README.md" not in proj.diff(second) and "NOTES.md" not in proj.diff(lead)
    assert sorted((i["task"], i["state"]) for i in inbox.items(proj)) == sorted([(lead, "ready"), (second, "ready")])

    monkeypatch.chdir(proj.root)
    assert main(["inbox"]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"request {lead}, 2 parts: {WORK}",
        f"  {lead}  ready     fixing the README typo",
        f"  {second}  ready     fixing the notes typo",
        "parallax show <task> for its card."]
    assert main(["task", "list"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"request {lead}, 2 parts: {WORK}" and out[1].startswith(f"  {lead}  [ready]")
    assert out[2].startswith(f"  {second}  [ready]") and out[2].endswith("fix the notes typo") and len(out) == 3

    accept(proj, lead)  # each Ready part is accepted on its own
    assert [i["task"] for i in inbox.items(proj)] == [second]
    accept(proj, second)
    assert proj.task(lead)["status"] == proj.task(second)["status"] == "accepted"
    counts = stats.touches(proj)
    assert (counts[lead], counts[second]) == (1, 1)
    cost = proj.task(lead)["cost_usd"] + proj.task(second)["cost_usd"]
    report = stats.report(proj)
    assert f"{'request':<8}{'parts':>8}{'touches':>8}{'cost':>9}" in report
    assert f"{lead:<8}{2:>8}{2:>8}{'$' + format(cost, '.2f'):>9}" in report


def test_no_more_than_max_parallel_parts_work_at_once(repo, started):
    proj = project(repo)
    lead = pilot.intake(proj, WORK + ", and the todo typo")["task"]
    focus = Focus(split_text(A, B, C))
    assert run_pilot(proj, lead, focus) == "held"
    second = other(proj, lead)
    assert len(proj.tasks()) == 2  # max_parallel is 2: the third part waits for a place
    [g] = conductor.groups(proj)
    assert g["parts"][2] == {"n": 3, "task": None, "goal": "fix the todo typo", "status": "waiting", "state": "waiting",
                             "waiting": "waiting for a part to finish"}
    assert run_pilot(proj, second, focus, "NOTES.md") == "ready"  # a place frees: the third starts
    third = other(proj, lead, second)
    assert started[-2:] == [(lead, "pilot"), (third, "pilot")]
    assert at(proj, "check.finished", second) < at(proj, "task.created", third)
    assert run_pilot(proj, third, focus, "TODO.md") == "ready"
    [rule] = [g["reason"] for g in kinds(proj, "gate.approved") if g["data"]["task"] == third]
    assert rule.endswith("this request's 3 parts together cap $6.70, within auto_launch_usd $10.00")


# parts in turn -----------------------------------------------------------------------------------------

def test_overlapping_parts_run_in_turn_the_later_on_the_earlier_ones_accepted_commit(repo, started):
    proj = project(repo)
    lead = pilot.intake(proj, "fix the README typo, then add a README section")["task"]
    focus = Focus(IN_TURN)
    assert run_pilot(proj, lead, focus) == "ready"  # nothing else drafts: it launched at once
    [s] = kinds(proj, "request.split")
    assert [p["after"] for p in s["data"]["parts"]] == [None, 1]
    views.board(proj)  # the housekeeping every board request runs
    assert list(proj.tasks()) == [lead]  # Ready, not accepted: the later part never starts on a tree you haven't accepted
    [g] = views.board(proj)["requests"]
    assert g["parts"][1]["task"] is None and g["parts"][1]["line"] == f"Part 2, waiting on task {lead}."

    acc = accept(proj, lead)
    second = other(proj, lead)
    t = proj.task(second)
    assert (t["base"], t["after"], t["part"]) == (acc["data"]["commit"], lead, 2) and started[-1] == (second, "pilot")
    assert (Path(t["worktree"]) / "README.md").read_text() == "fixed\n"  # the earlier part's accepted change is there
    assert run_pilot(proj, second, focus, "README.md", "fixed\n\n## More\n") == "ready"
    diff = proj.diff(second)
    assert "+## More" in diff and "-fixed" not in diff  # its change alone, on top of the first


def test_when_the_earlier_part_is_dropped_the_later_one_comes_to_you(repo, started):
    proj = project(repo)
    lead = pilot.intake(proj, "fix the README typo, then add a README section")["task"]
    focus = Focus(IN_TURN)
    assert run_pilot(proj, lead, focus) == "ready"
    dropped(proj, lead, "not this way")
    second = other(proj, lead)
    assert [(i["task"], i["state"]) for i in inbox.items(proj)] == [(second, "needs you")]
    dec = decide.decision(proj, second)
    assert (dec.kind, dec.recommend, [o.name for o in dec.options]) == ("waiting", "rebase", ["rebase", "drop"])
    assert dec.question.startswith(f"Task {lead}, which this part was to follow, ended without an accept")
    card = show.report(proj, second)
    assert linted(proj, card) and f"Next: you run parallax decide {second} rebase." in card
    assert proj.task(second)["base"] == proj.task(lead)["base"]  # the base branch as it is: the drop moved nothing
    assert decide.apply(proj, second, "rebase") == f"drafting {second} without you. it comes back to the inbox."
    assert started[-1] == (second, "pilot")
    assert run_pilot(proj, second, focus, "README.md", "added\n") == "ready"


# a split that doesn't hold ------------------------------------------------------------------------------

def test_a_split_over_max_parts_goes_back_to_focus(repo, started):
    proj = project(repo, ROOMY.replace("max_parts = 4", "max_parts = 2"))
    lead = pilot.intake(proj, WORK + ", and the todo typo")["task"]
    focus = Focus([split_text(A, B, C), TWO])
    assert run_pilot(proj, lead, focus) == "held"
    [m] = kinds(proj, "draft.misfit")
    assert m["data"]["doc"] == "split" and "the split has 3 parts, and the policy allows at most 2" in focus.requests[1]
    assert len(kinds(proj, "request.split")[0]["data"]["parts"]) == 2


def test_a_protected_path_in_a_split_is_redrafted_then_comes_to_you_as_one_decision(repo, started):
    proj = project(repo)
    lead = pilot.intake(proj, WORK)["task"]
    focus = Focus(split_text(A, ("tidy the rules", "CLAUDE.md")))
    assert run_pilot(proj, lead, focus) == "stuck"
    firsts = [r.split("\n", 1)[0] for r in focus.requests]
    assert firsts == [f"Draft docs/tasks/{lead}/split.md in exactly this shape:"] * 3  # two redrafts, then you
    assert "part 2's scope names CLAUDE.md, which is protected" in focus.requests[-1]
    assert [(i["task"], i["state"]) for i in inbox.items(proj)] == [(lead, "needs you")] and list(proj.tasks()) == [lead]
    dec = decide.decision(proj, lead)
    assert (dec.kind, dec.recommend) == ("split", "one task") and "CLAUDE.md, which is protected" in dec.item["reason"]
    assert linted(proj, show.report(proj, lead))
    assert decide.apply(proj, lead, "one task") == f"drafting {lead} without you. it comes back to the inbox."
    [yours] = kinds(proj, "request.split")  # the split never held, so the one recorded is your answer
    assert yours["actor"] == "human" and len(yours["data"]["parts"]) == 1
    assert run_pilot(proj, lead, FakeDrafter(docs())) == "ready"  # the whole request, as one task
    assert list(proj.tasks()) == [lead]


# the launch: one question only when needed --------------------------------------------------------------

def test_one_question_only_when_the_parts_caps_together_are_over_auto_launch_usd(repo, started):
    proj = project(repo, CONDUCTOR)  # auto_launch_usd $3.00: each part's cap fits alone, the two don't
    lead = pilot.intake(proj, WORK)["task"]
    focus = Focus(TWO)
    assert run_pilot(proj, lead, focus) == "held"
    second = other(proj, lead)
    assert run_pilot(proj, second, focus, "NOTES.md") == "held"
    [ask] = kinds(proj, "review.requested")
    assert ask["data"]["task"] == lead and ask["data"]["parts"] == [lead, second]
    assert ask["reason"] == f"this request's 2 parts together cap $4.50 ({lead} $2.30, {second} $2.20), over auto_launch_usd ($3.00)"
    assert kinds(proj, "gate.approved") == []
    assert [(i["task"], i["state"]) for i in inbox.items(proj)] == [(lead, "needs you")]  # one question for both
    dec = decide.decision(proj, lead)
    assert (dec.kind, dec.question) == ("launch", "Launch this request's 2 parts, with caps of $4.50 in all?")
    assert "waits for your answer on task " + lead in show.report(proj, second)
    [row] = [r for r in views.board(proj)["working"] if r["task"] == second]
    assert row["line"].startswith("Its plan fits, and it waits for your answer on task") and row["request"] == {
        "id": lead, "part": 2, "of": 2}

    assert decide.apply(proj, lead, "launch") == f"building {lead} without you. it comes back to the inbox."
    approved = {g["data"]["task"]: g for g in kinds(proj, "gate.approved")}
    assert approved[lead]["actor"] == approved[second]["actor"] == "human"
    assert approved[second]["data"]["answer"] == approved[lead]["id"]  # your one launch, carried to the other part
    assert (lead, "build") in started and started[-1] == (second, "pilot")
    assert run_pilot(proj, second, focus, "NOTES.md") == "ready"
    counts = stats.touches(proj)
    assert (counts[lead], counts[second]) == (1, 0)
