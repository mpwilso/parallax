import os
from pathlib import Path

import pytest

from fakes import FakeDrafter
from parallax import approvals, build, lifecycle, lint
from parallax.agents.claude import tool_to_action
from parallax.cli import main
from parallax.core import POLICY_FILE, ROOT_ENV, TASK_ENV, ParallaxError, Project
from parallax.gate import make_permission_fn
from parallax.policy import Policy

INTENT = """\
Bottom line: Fix the README install steps so they work in WSL.
Not looked at: nothing

kind: docs
size: {size}
title: fixing the README install steps
scope: README.md, tests/**, Makefile, extra.py, pytest.ini, tox.ini

## Problem
The steps assume PowerShell.

## Outcome
1. A new user on WSL can follow them.

## Constraints
Keep the macOS steps.
"""
SPEC = "Bottom line: One install section per platform.\nNot looked at: nothing\n\n## Design\nSplit it.\n"
PLAN = """\
Bottom line: Rewrite the install section for WSL.
Not looked at: nothing

## Steps
1. Edit README.md.

## Tests
tests/test_readme.py checks the steps.

```toml
files = ["README.md", "tests/test_readme.py"]
tests = ["tests/test_readme.py"]
lines_changed = 30
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = "{tightening}"
estimated_cost_usd = 0.9
budget_cap_usd = 2.0
covers = {{ "1" = ["tests/test_readme.py"] }}
```
"""
WANT = "the README install steps are wrong for WSL"


def docs(size="small", tightening=""):
    return {"intent": INTENT.format(size=size), "spec": SPEC, "plan": PLAN.format(tightening=tightening)}


def make_key() -> Path:
    path = approvals.key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("ab" * 32 + "\n")
    os.chmod(path, 0o600)
    return path


@pytest.fixture
def proj(repo):
    make_key()
    return Project.init(repo)


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


# intent new and the small gate -------------------------------------------------------------

def test_drafting_writes_intent_and_plan_for_a_small_task(proj, monkeypatch, capsys):
    """Drafting itself; `parallax do` (M12) is what runs it now. See test_m12."""
    drafter = FakeDrafter(docs())
    tid = lifecycle.new_intent(proj, WANT, drafter)["task"]
    folder = proj.root / "docs" / "tasks" / tid
    assert (folder / "intent.md").read_text() == docs()["intent"]
    assert sorted(p.name for p in folder.iterdir()) == ["intent.md", "plan.md"]
    assert WANT in drafter.requests[0] and "The intent:" in drafter.requests[1]
    assert [e["data"]["doc"] for e in kinds(proj, "draft.recorded")] == ["intent", "plan"]
    assert proj.task(tid)["cost_usd"] == pytest.approx(0.2)
    assert drafter.caps == [proj.policy.budget["drafting_usd"]] * 2
    assert proj.task(tid)["branch"] == f"parallax/{tid}-readme-install-steps"

    monkeypatch.chdir(proj.root)
    assert main(["lint", f"docs/tasks/{tid}/plan.md"]) == 0
    assert capsys.readouterr().out == "ok\n"


def test_approving_the_gate_needs_no_reason_and_signs_the_file_hashes(proj, monkeypatch, capsys):
    t = lifecycle.new_intent(proj, WANT, FakeDrafter(docs(tightening="check the WSL steps by hand")))
    tid = t["task"]
    monkeypatch.chdir(proj.root)
    assert main(["approve", tid]) == 0
    assert capsys.readouterr().out.startswith(f"approved intent and plan for {tid} (ledger ")

    [g] = kinds(proj, "gate.approved")
    folder = lifecycle.task_dir(proj, tid)
    assert g["actor"] == "human" and g["data"]["gate"] == "intent+plan"
    assert g["data"]["files"] == {d: lifecycle.file_hash(folder / f"{d}.md") for d in ("intent", "plan")}
    assert approvals.valid(approvals.load_key(), g["data"])
    assert lifecycle.state(proj, tid).gate is None
    assert lifecycle.status_line(proj, tid) == "plan approved"
    with pytest.raises(ParallaxError, match="already approved"):
        lifecycle.approve(proj, tid)


# the large gate ------------------------------------------------------------------------------

def test_a_large_task_approves_intent_then_spec_and_plan(proj):
    drafter = FakeDrafter(docs(size="large"))
    tid = lifecycle.new_intent(proj, WANT, drafter)["task"]
    assert not lifecycle.doc_path(proj, tid, "plan").exists()
    assert lifecycle.approve(proj, tid)["data"]["gate"] == "intent"
    lifecycle.draft(proj, tid, ["spec", "plan"], drafter)
    assert "The spec:" in drafter.requests[-1]  # the plan drafter sees the spec
    lifecycle.approve(proj, tid)
    assert [g["data"]["gate"] for g in kinds(proj, "gate.approved")] == ["intent", "spec+plan"]
    assert lifecycle.state(proj, tid).gate is None


def test_an_approved_file_that_changed_blocks_the_next_gate(proj):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs(size="large")))["task"]
    lifecycle.approve(proj, tid)
    lifecycle.draft(proj, tid, ["spec", "plan"], FakeDrafter(docs(size="large")))
    path = lifecycle.doc_path(proj, tid, "intent")
    path.write_text(path.read_text() + "\nAlso rewrite the whole README.\n")
    with pytest.raises(ParallaxError, match="intent.md changed after you approved it"):
        lifecycle.approve(proj, tid)


# signatures ------------------------------------------------------------------------------------

def test_an_approval_without_a_valid_signature_doesnt_count(proj):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    files = {d: lifecycle.file_hash(lifecycle.doc_path(proj, tid, d)) for d in ("intent", "plan")}
    proj.ledger.append("gate.approved", "human", "", task=tid, gate="intent+plan", files=files, sig="0" * 64)
    proj.ledger.append("gate.approved", "human", "", task=tid, gate="intent+plan", files=files)
    other = lifecycle.new_intent(proj, "another task", FakeDrafter(docs()))["task"]
    real = lifecycle.approve(proj, other)
    proj.ledger.append("gate.approved", "human", "", task=tid, gate="intent+plan", files=files,
                       sig=real["data"]["sig"])  # a real signature, copied from another task
    assert lifecycle.state(proj, tid).gate == ("intent", "plan")


def test_no_key_means_no_approval_and_nothing_counts(proj):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    lifecycle.approve(proj, tid)
    key = approvals.key_path()
    os.chmod(key, 0o644)
    with pytest.raises(ParallaxError, match="readable by others"):
        approvals.load_key()
    key.unlink()
    assert lifecycle.state(proj, tid).gate == ("intent", "plan")  # fails closed
    other = lifecycle.new_intent(proj, "x", FakeDrafter(docs()))["task"]
    with pytest.raises(ParallaxError, match="parallax doctor"):
        lifecycle.approve(proj, other)


def test_a_task_cant_approve_its_own_gate(proj, monkeypatch):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    monkeypatch.setenv(TASK_ENV, tid)
    monkeypatch.setenv(ROOT_ENV, str(proj.root))
    with pytest.raises(ParallaxError, match="tasks can't"):
        lifecycle.approve(proj, tid)
    assert kinds(proj, "gate.approved") == []


# lint, reject, redraft ----------------------------------------------------------------------------

def test_a_plan_that_fails_lint_cant_be_approved(proj):
    bad = docs()
    bad["plan"] = bad["plan"].replace('"README.md", ', '"README.md", "CLAUDE.md", ')
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(bad))["task"]
    assert f"docs/tasks/{tid}/plan.md:10 files: CLAUDE.md is protected" in "\n".join(
        lifecycle.lint_problems(proj, tid, ["plan"]))
    with pytest.raises(ParallaxError, match="CLAUDE.md is protected"):
        lifecycle.approve(proj, tid)


def test_reject_needs_a_reason_and_the_redraft_hears_it(proj, monkeypatch, capsys):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs()))["task"]
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    monkeypatch.chdir(proj.root)
    assert main(["reject", tid]) == 1
    assert "needs a reason" in capsys.readouterr().err
    assert main(["reject", tid, "--reason", "the plan skips the uv step"]) == 0
    [r] = kinds(proj, "task.redraft")
    assert r["reason"] == "the plan skips the uv step"

    drafter = FakeDrafter(docs())
    lifecycle.draft(proj, tid, ["intent", "plan"], drafter)  # what the pilot runs after a reject
    assert "the plan skips the uv step" in drafter.requests[0] and "the plan skips the uv step" in drafter.requests[1]


def test_a_failed_draft_is_decision_needed(proj):
    tid = lifecycle.new_intent(proj, WANT, FakeDrafter(docs(), fail={"plan"}))["task"]
    text = lifecycle.report(proj, tid)
    assert "Type: Decision needed" in text
    assert "Bottom line: Drafting plan.md stopped: stopped at the budget cap ($2.0)." in text
    [f] = kinds(proj, "draft.failed")
    assert f["data"]["doc"] == "plan" and f["data"]["cost_usd"] == 0.1


# the drafters' boundary ----------------------------------------------------------------------------

def test_drafters_read_only_inside_the_worktree(proj, tmp_path):
    outside = tmp_path / "secret.txt"
    drafter = FakeDrafter(docs(), reads=["README.md", "src/../README.md", str(outside), "../../x", "/etc/passwd"])
    lifecycle.new_intent(proj, WANT, drafter)
    allowed = [(path, p.allowed) for path, p in drafter.results[:5]]
    assert allowed == [("README.md", True), ("src/../README.md", True), (str(outside), False),
                       ("../../x", False), ("/etc/passwd", False)]


def test_glob_and_grep_are_judged_by_their_paths(proj):
    t = proj.new_task("x")
    fn = make_permission_fn(proj, t["task"], Path(t["worktree"]), read_only=True)
    for tool, args, ok in [("Glob", {"pattern": "**/*.py"}, True), ("Glob", {"pattern": "/home/**/*.key"}, False),
                           ("Glob", {"pattern": "*.py", "path": "/etc"}, False), ("Grep", {"pattern": "/etc"}, True),
                           ("Grep", {"pattern": "x", "path": "/root"}, False)]:
        action, detail, paths = tool_to_action(tool, args)
        assert fn(action, detail, paths).allowed is ok, (tool, args)


def test_drafting_cap_comes_from_the_policy(repo):
    (repo / POLICY_FILE).write_text('[budget]\ndrafting_usd = 0.5\n')
    make_key()
    proj = Project.init(repo)
    drafter = FakeDrafter(docs())
    lifecycle.new_intent(proj, WANT, drafter)
    assert drafter.caps == [0.5, 0.5]
    for bad in ({"drafting_usd": 0}, {"drafting_usd": True}, {"other": 1.0}):
        with pytest.raises(ValueError):
            Policy({}, budget=bad)


# lint rules ------------------------------------------------------------------------------------------

GOOD = "Type: FYI\nBottom line: All good.\nNot looked at: nothing\nNext: nobody does anything.\n"


def messages(problems):
    return " | ".join(m for _, m in problems)


def test_lint_report_header_rules():
    assert lint.lint_report(GOOD) == []
    assert lint.lint_report("**Type:** FYI\n**Bottom line:** Fine.\n**Not looked at:** nothing\n**Next:** none.\n") == []
    assert "expected 'Type:'" in messages(lint.lint_report(GOOD.replace("Type: FYI\n", "")))
    assert "Type must be one of" in messages(lint.lint_report(GOOD.replace("FYI", "Urgent")))
    assert "Not looked at is empty" in messages(lint.lint_report(GOOD.replace("nothing", "")))
    assert "one sentence" in messages(lint.lint_report(GOOD.replace("All good.", "All good. Really.")))
    assert "no em dashes" in messages(lint.lint_report(GOOD + "\u2014\n"))
    long = GOOD.replace("All good.", " ".join(["word"] * 40) + ".")
    assert "keep it under 40" in messages(lint.lint_report(long))


def test_lint_report_body_rules(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")
    decide = "Decisions\n- Decide: merge it? Recommend: yes. Blocks: nothing.\n"
    assert lint.lint_report(GOOD + decide) == []
    assert "decision line must read" in messages(lint.lint_report(GOOD + "Decisions\n- merge it?\n"))
    assert "Recommended only when" in messages(lint.lint_report(GOOD + decide + "Recommended\n- ship\n"))
    assert "out of order" in messages(lint.lint_report(GOOD + "Found\n- a.py:1 x\nDecisions\n"))
    assert "unknown section" in messages(lint.lint_report(GOOD + "## Thoughts\n"))
    assert "Changed since last time" in messages(lint.lint_report(GOOD, revisit=True))

    found = "Found\n- a.py:1 sets x\n- ledger 1234abcd recorded it\n- a guess, Unverified\n"
    assert lint.lint_report(GOOD + found, root=tmp_path, ledger_ids={"1234abcd"}) == []
    for item in ("- a.py:9 no such line", "- nope.py:1 no such file", "- ledger ffffffff unknown", "- no source"):
        assert "cites no source" in messages(lint.lint_report(GOOD + "Found\n" + item + "\n", root=tmp_path,
                                                             ledger_ids={"1234abcd"}))
    wordy = GOOD + "Found\n- a.py:1 " + " ".join(["word"] * 150) + "\n"
    assert "keep it under 150" in messages(lint.lint_report(wordy, root=tmp_path))
    assert lint.lint_report(GOOD + "## Details\n" + " ".join(["word"] * 300) + "\n") == []  # Details is free


def test_lint_lifecycle_files():
    assert lint.lint_lifecycle(docs()["intent"], "intent") == []
    assert lint.lint_lifecycle(docs()["plan"], "plan") == []
    assert "Type" not in messages(lint.lint_lifecycle(docs()["plan"], "plan"))  # no Type in stored files
    assert "size:" in messages(lint.lint_lifecycle(docs()["intent"].replace("size: small", "size: huge"), "intent"))
    assert "## Outcome" in messages(lint.lint_lifecycle(docs()["intent"].replace("## Outcome", "## Result"), "intent"))
    plan = docs()["plan"]
    for change, why in [(("budget_cap_usd = 2.0", "budget_cap_usd = 0.5"), "below estimated"),
                        (("lines_changed = 30\n", ""), "missing lines_changed"),
                        (('domains = []', 'domains = ["not a host"]'), "plain host name"),
                        (('"README.md", ', '"../outside.md", '), "inside the repo"),
                        (('"README.md", ', '"docs/tasks/x/plan.md", '), "is protected"),
                        (("```\n", "```\nmore text\n"), "last thing"),
                        (("```toml", "```python"), "no ```toml block")]:
        assert why in messages(lint.lint_lifecycle(plan.replace(*change), "plan")), why


def test_cli_lint_knows_a_lifecycle_file_by_its_place(proj, monkeypatch, capsys):
    folder = proj.root / "docs" / "tasks" / "abc123"
    folder.mkdir(parents=True)
    (folder / "plan.md").write_text(docs()["plan"])
    (proj.root / "report.md").write_text(docs()["plan"])  # the same text is not a valid report
    monkeypatch.chdir(proj.root)
    assert main(["lint", "docs/tasks/abc123/plan.md"]) == 0
    assert main(["lint", "report.md"]) == 1
    assert "report.md:1 header: expected 'Type:' here" in capsys.readouterr().out
