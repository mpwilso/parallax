import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from fakes import FakeChecker, ScriptedAgent, good_probe
from parallax import build, lifecycle, lint, secretscan
from parallax.accept import accept, confirm_merges
from parallax.agents.base import Finding, Review
from parallax.cli import main
from test_check import approved, built, kinds, run

GIT = ["git", "-c", "user.email=t@t", "-c", "user.name=Matt"]


def ready(repo, content="ok\n", checker=None):
    proj, tid, wt = approved(repo)
    maker = ScriptedAgent(steps=[("write", "README.md", content)])
    maker.model = "claude-opus-5"
    assert build.run_build(proj, tid, lambda left, settings: maker, preflight_runner=good_probe) == "built"
    assert run(proj, tid, maker, checker) == "ready"
    return proj, tid, wt


def git(repo, *args):
    return subprocess.run([*GIT, "-C", str(repo), *args], capture_output=True, text=True)


# accept, then the merge it prints ------------------------------------------------------------

def test_accept_then_the_printed_merge_really_merges(repo, monkeypatch, capsys):
    proj, tid, wt = ready(repo)
    monkeypatch.chdir(repo)
    assert main(["accept", tid]) == 0
    [acc] = kinds(proj, "task.accepted")
    branch = proj.task(tid)["branch"]
    assert capsys.readouterr().out == (f"accepted {tid} as {acc['data']['commit'][:7]}. merge it yourself:\n"
                                       f"git merge --ff-only {branch}\n")

    merged = git(repo, "merge", "--ff-only", branch)  # exactly what it printed
    assert merged.returncode == 0, merged.stderr
    assert (repo / "README.md").read_text() == "ok\n"
    tracked = git(repo, "ls-files", f"docs/tasks/{tid}").stdout.split()
    assert tracked == [f"docs/tasks/{tid}/{d}.md" for d in ("intent", "plan", "record")]

    main(["task", "list"])  # any next command confirms the merge
    assert f"task {tid}: you merged it unchanged. recorded." in capsys.readouterr().out
    assert proj.task(tid)["status"] == "merged"
    assert confirm_merges(proj) == []


def test_the_commit_is_exactly_the_reviewed_tree_plus_the_task_docs(repo):
    proj, tid, wt = ready(repo)
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch {shlex.quote(str(repo / 'HOOK-RAN'))}\n")  # a path may hold a space
    hook.chmod(0o755)
    acc = accept(proj, tid)["data"]
    reviewed = kinds(proj, "check.staged")[-1]["data"]["tree"]
    def listing(treeish):
        return git(repo, "ls-tree", "-r", treeish).stdout.splitlines()
    ours = [line for line in listing(acc["commit"]) if f"docs/tasks/{tid}/" not in line]
    assert ours == listing(reviewed)
    assert git(repo, "rev-parse", f"{acc['commit']}^").stdout.strip() == proj.task(tid)["base"]
    assert not (repo / "HOOK-RAN").exists()  # commit-tree runs no hooks
    assert git(repo, "rev-parse", proj.task(tid)["branch"]).stdout.strip() == acc["commit"]
    assert not lifecycle.task_dir(proj, tid).exists()  # moved aside, so the merge can bring it back
    assert (Path(acc["docs_moved_to"]) / "record.md").exists()


def test_the_commit_message_ends_with_the_trailers(repo):
    proj, tid, wt = ready(repo)
    acc = accept(proj, tid)["data"]
    body = git(repo, "log", "-1", "--format=%B", proj.task(tid)["branch"]).stdout
    assert body.startswith("Fixing the README install steps\n\nRecord: docs/tasks/")
    parsed = subprocess.run(["git", "interpret-trailers", "--parse"], input=body, capture_output=True, text=True).stdout
    keys = [line.split(":", 1)[0] for line in parsed.splitlines()]
    assert keys == ["Parallax-Task", "Intent", "Approved-By", "Verified-By", "Ledger-Head"]
    assert f"Parallax-Task: {tid}" in parsed and f"Intent: docs/tasks/{tid}/intent.md" in parsed
    assert "Verified-By: checker=pass tests=3/3" in parsed
    assert parsed.splitlines()[2].startswith("Approved-By: ") and "(intent+plan) " in parsed
    assert f"Ledger-Head: {acc['ledger_head']}" in parsed
    assert proj.ledger.verify()[0]


def test_when_the_base_moved_on_it_prints_a_plain_merge(repo, capsys, monkeypatch):
    proj, tid, wt = ready(repo)
    (repo / "other.txt").write_text("x\n")
    git(repo, "add", "other.txt")
    git(repo, "commit", "-qm", "meanwhile")
    monkeypatch.chdir(repo)
    main(["accept", tid])
    assert capsys.readouterr().out.splitlines()[-1] == f"git merge {proj.task(tid)['branch']}"


# what blocks accept --------------------------------------------------------------------------------

def test_accept_needs_ready_and_the_approved_files_as_approved(repo):
    proj, tid, wt = approved(repo)
    with pytest.raises(Exception, match="not ready"):
        accept(proj, tid)
    run(proj, tid, built(proj, tid, [("write", "README.md", "ok\n")]))
    plan = lifecycle.doc_path(proj, tid, "plan")
    plan.write_text(plan.read_text() + "\nsneaky\n")
    with pytest.raises(Exception, match="doesn't match the version you approved"):
        accept(proj, tid)
    assert not kinds(proj, "task.accepted")


def test_a_secret_blocks_accept_unless_you_accept_the_risk(repo, monkeypatch, capsys):
    key = "sk-ant-" + "a1B2" * 10
    proj, tid, wt = ready(repo, content=f'API = "{key}"\n')
    monkeypatch.chdir(repo)
    assert main(["accept", tid]) == 1
    assert "README.md:1 anthropic key" in capsys.readouterr().err
    assert main(["accept", tid, "--reason", "a test fixture, not a live key"]) == 0
    assert kinds(proj, "risk.accepted")[0]["reason"] == "a test fixture, not a live key"


def test_the_scan_finds_added_lines_only_with_their_line_numbers():
    diff = ("+++ b/a.py\n@@ -1,2 +1,3 @@\n keep\n-old = 'AKIAABCDEFGHIJKLMNOP'\n+new = 1\n"
            "+aws = 'AKIAABCDEFGHIJKLMNOP'\n+++ b/k.pem\n@@ -0,0 +1 @@\n+-----BEGIN RSA PRIVATE KEY-----\n")
    assert secretscan.scan_diff(diff) == ["a.py:3 aws access key", "k.pem:1 private key"]


def test_gitleaks_runs_when_installed(tmp_path, monkeypatch):
    fake = tmp_path / "bin" / "gitleaks"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\nimport json, sys\na = sys.argv\n"
                    "open(a[a.index('--report-path') + 1], 'w').write(json.dumps("
                    "[{'File': a[a.index('--source') + 1] + '/x.py', 'StartLine': 4, 'RuleID': 'generic-api-key'}]))\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}:{os.environ['PATH']}")
    assert secretscan.gitleaks({"x.py": b"y"}) == (["x.py:4 generic-api-key (gitleaks)"], "")
    monkeypatch.setenv("PATH", "/nonexistent")
    assert secretscan.gitleaks({"x.py": b"y"}) == ([], "gitleaks isn't installed")


# the record --------------------------------------------------------------------------------------

def test_the_record_is_generated_from_the_ledger_and_lints(repo):
    finding = Review("pass", [Finding("minor", "README.md:1", "could say which shell")], "the rendered page")
    proj, tid, wt = ready(repo, checker=FakeChecker(reviews=[finding]))
    acc = accept(proj, tid)["data"]
    text = (Path(acc["docs_moved_to"]) / "record.md").read_text()
    assert text.startswith(f"Type: FYI\nBottom line: Task {tid}, fixing the README install steps, was accepted")
    ids = {e["id"] for e in proj.ledger.entries()}
    shutil.copytree(acc["docs_moved_to"], lifecycle.task_dir(proj, tid))  # lint cites need the files in place
    assert lint.lint_report(text, root=proj.root, ledger_ids=ids) == []
    for want in ("Files changed: README.md.", f"Why: docs/tasks/{tid}/intent.md, docs/tasks/{tid}/plan.md.",
                 "signed with the approval key", "Written by: Maker, which builds in the sandbox (claude-opus-5), in 1 run in the sandbox.",
                 "Verified by: Parallax ran the plan's tests in the sandbox (3 of 3 passed)",
                 f"Rollback: revert the commit whose message has Parallax-Task: {tid}",
                 "Known risks (agent-written, from Second Eye): Minor, at README.md:1: could say which shell.",
                 "Not looked at: Second Eye says: the rendered page."):
        assert want in text, want


@pytest.mark.skipif(not shutil.which("ssh-keygen"), reason="needs ssh-keygen")
def test_the_accept_commit_is_signed_when_you_have_a_key(repo, tmp_path):
    key = tmp_path / "signing"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    git(repo, "config", "gpg.format", "ssh")
    git(repo, "config", "user.signingkey", f"{key}.pub")
    proj, tid, wt = ready(repo)
    acc = accept(proj, tid)["data"]
    assert acc["signed"] is True
    assert "gpgsig" in git(repo, "cat-file", "commit", acc["commit"]).stdout


def test_the_scan_knows_the_common_key_shapes():
    keys = {
        "openai-style key": "sk-proj-" + "a1" * 20,
        "gitlab token": "glpat-" + "x" * 20,
        "slack webhook": "https://hooks.slack.com/services/T000/B000/abc",
        "stripe key": "sk_live_" + "a" * 24,
        "npm token": "npm_" + "a" * 36,
        "pypi token": "pypi-" + "A" * 60,
        "hugging face token": "hf_" + "a" * 34,
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop",
    }
    diff = "+++ b/x.py\n@@ -0,0 +1 @@\n" + "".join(f"+v = '{v}'\n" for v in keys.values())
    assert secretscan.scan_diff(diff) == [f"x.py:{n} {k}" for n, k in enumerate(keys, start=1)]
    assert secretscan.scan_diff("+++ b/x.py\n@@ -0,0 +1 @@\n+v = 'sk_live_short'\n") == []


# Accept and merge: your click, fast-forward only, local, never pushed -----------------------------------

def _pushes(monkeypatch):
    """Every git command run from here on; a push among them fails the test."""
    ran, real = [], subprocess.run

    def spy(argv, *a, **k):
        if argv and argv[0] == "git":
            ran.append(argv)
            assert "push" not in argv, "Parallax never pushes"
        return real(argv, *a, **k)
    monkeypatch.setattr(subprocess, "run", spy)
    return ran


def test_accept_and_merge_fast_forwards_the_base_branch_and_never_pushes(repo, monkeypatch):
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    ran = _pushes(monkeypatch)
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    target = acc["data"]["target"]
    assert out == {"message": f"merged {tid} into {target}, fast-forward: no test command is configured ([merge] "
                              "test_command), so no tests ran first; nothing was pushed.", "merged": True}
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == acc["data"]["commit"] and (repo / "README.md").read_text() == "ok\n"
    assert proj.task(tid)["status"] == "merged"
    [click] = kinds(proj, "merge.clicked")
    assert click["actor"] == "human"  # the merge is your click, recorded as yours
    assert any(argv[-2:] == ["-q", acc["data"]["branch"]] and "--ff-only" in argv for argv in ran)


def moved_on(repo, name="other.txt", text="moved on\n"):
    """The base branch gains a commit after the task began. Returns where it now is."""
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-qm", "the base moved on")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_a_moved_base_is_merged_into_the_task_and_lands_on_one_click(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    moved = moved_on(repo)
    runner = Runner(0, "432 passed in 150s")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", runner)
    ran = _pushes(monkeypatch)
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    target, commit = acc["data"]["target"], acc["data"]["commit"]
    head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert git(repo, "rev-parse", f"{head}^@").stdout.split() == [commit, moved]  # a real merge, both sides
    assert runner.calls == [(head, "scripts/test.sh")]  # the gate, once, on the commit that lands
    assert out == {"message": f"merged {tid} into {target}, {target} merged in first: its tests passed first "
                              "(scripts/test.sh); nothing was pushed.", "merged": True}
    assert (repo / "README.md").read_text() == "ok\n" and (repo / "other.txt").read_text() == "moved on\n"
    tracked = git(repo, "ls-files", f"docs/tasks/{tid}").stdout.split()
    assert tracked == [f"docs/tasks/{tid}/{d}.md" for d in ("intent", "plan", "record")]
    assert proj.task(tid)["status"] == "merged"  # the accepted commit is an ancestor of the branch
    assert not any("push" in argv for argv in ran)
    [click] = kinds(proj, "merge.clicked")
    assert click["actor"] == "human" and click["data"]["ff"] is False


def test_a_conflict_merging_the_moved_base_in_moves_nothing(repo, monkeypatch):
    from parallax import accept as acc_mod, sandbox
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    before = moved_on(repo, "README.md", "theirs\n")  # the same file the task wrote
    runner = Runner(0, "432 passed in 150s")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", runner)
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    target, commit, branch = acc["data"]["target"], acc["data"]["commit"], acc["data"]["branch"]
    assert out["message"] == (f"accepted {tid} as {commit[:7]}, but merging {target} into it hit a conflict in "
                              f"README.md, so {target} didn't move. to resolve it, in your repo's folder: git checkout "
                              f"{branch}, git merge {target}, fix README.md and git add them, git commit --no-edit, then "
                              f"git checkout {target} and git merge --ff-only {branch}. merge it yourself:")
    assert "\n" not in out["message"] and out["merge"] == "\n".join(CONFLICT_STEPS(branch, target))  # yours to run
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before  # nothing merged, nothing forced
    assert (repo / "README.md").read_text() == "theirs\n"
    assert git(repo, "rev-parse", branch).stdout.strip() == commit  # the task branch is where accept left it
    assert runner.calls == []  # no gate: there was nothing to test
    assert not (sandbox.data_home() / "merge-in" / commit[:12]).exists()
    assert "merge-in" not in git(repo, "worktree", "list").stdout  # no throwaway worktree left behind
    assert proj.task(tid)["status"] == "accepted" and not kinds(proj, "merge.clicked")


def CONFLICT_STEPS(branch, target):
    return [f"git checkout {branch}", f"git merge {target}", "# fix the conflict in README.md", "git add README.md",
            "git commit --no-edit", f"git checkout {target}", f"git merge --ff-only {branch}"]


def test_the_conflict_commands_resolve_it_when_run_as_given(repo, monkeypatch):
    """The card's commands, run one by one in your folder, with the conflict fixed by hand, land it."""
    from parallax import accept as acc_mod, show, views
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    moved_on(repo, "README.md", "theirs\n")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", Runner(0, "ok"))
    act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    target, branch = acc["data"]["target"], acc["data"]["branch"]
    card = views.card(proj, tid)
    assert card["merge_note"] == (f"Accepted, but merging {target} into it hit a conflict in README.md, so {target} "
                                  "didn't move. Resolve it with these commands in your repo's folder:")
    assert card["merge"].splitlines() == CONFLICT_STEPS(branch, target)
    assert show.report(proj, tid).splitlines()[3] == (
        f"Next: you resolve it: git checkout {branch}, git merge {target}, fix README.md and git add them, git commit "
        f"--no-edit, then git checkout {target} and git merge --ff-only {branch}.")
    for step in CONFLICT_STEPS(branch, target):
        if step.startswith("#"):
            (repo / "README.md").write_text("ok, and theirs\n")  # the one step that's yours
            continue
        out = subprocess.run(step.split(), cwd=repo, capture_output=True, text=True)
        assert out.returncode in (0, 1) if step == f"git merge {target}" else out.returncode == 0, (step, out.stderr)
    assert git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip() == target
    assert (repo / "README.md").read_text() == "ok, and theirs\n"
    acc_mod.confirm_merges(proj)  # what your next parallax command does first
    assert proj.task(tid)["status"] == "merged"  # the accepted commit is in what landed


def test_a_failing_gate_after_the_moved_base_merge_leaves_the_branches_alone(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    before = moved_on(repo)
    runner = Runner(1, "....F\nFAILED tests/test_merge.py::test_both_sides - AssertionError\n1 failed, 431 passed\n")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", runner)
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    target, commit, branch = acc["data"]["target"], acc["data"]["commit"], acc["data"]["branch"]
    first = "FAILED tests/test_merge.py::test_both_sides - AssertionError"
    assert out["message"] == (f"accepted {tid} as {commit[:7]}, but its tests failed on the commit it would land, so "
                              f"{target} didn't move. first failure: {first}. fix that, then merge by hand. "
                              "merge it yourself:")
    assert [c for _, c in runner.calls] == ["scripts/test.sh"] and runner.calls[0][0] != commit  # the merge commit
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before  # the base branch didn't move
    assert git(repo, "rev-parse", branch).stdout.strip() == commit  # nor did the task branch
    assert proj.task(tid)["status"] == "accepted" and not kinds(proj, "merge.clicked")
    [t] = kinds(proj, "merge.tested")
    assert (t["data"]["ok"], t["data"]["merged_in"]) == (False, True)


def test_a_cherry_picked_copy_of_the_accepted_commit_is_not_a_merge(repo):
    """The merged rule is unchanged: only the accepted commit itself, reachable from the branch."""
    proj, tid, wt = ready(repo)
    acc = accept(proj, tid)["data"]
    moved_on(repo)
    picked = git(repo, "cherry-pick", acc["commit"])
    assert picked.returncode == 0, picked.stderr
    assert git(repo, "rev-parse", "HEAD").stdout.strip() != acc["commit"]
    assert confirm_merges(proj) == [] and proj.task(tid)["status"] == "accepted"


def test_merge_now_wont_merge_into_any_branch_but_the_one_accept_recorded(repo):
    from parallax.accept import merge_now
    from parallax.core import ParallaxError
    proj, tid, wt = ready(repo)
    target = accept(proj, tid)["data"]["target"]
    git(repo, "switch", "-qc", "elsewhere")
    with pytest.raises(ParallaxError, match=f"can't merge from here: your checkout is on elsewhere, not {target}"):
        merge_now(proj, tid)
    assert proj.task(tid)["status"] == "accepted" and not kinds(proj, "merge.clicked")


# Accept and merge runs the project's tests first, on the exact commit it would land ------------------------

def _with_merge_tests(repo, proj, command="make test"):
    from parallax.core import POLICY_FILE
    policy = repo / POLICY_FILE
    policy.write_text(policy.read_text().replace('test_command = ""              # Accept and merge',
                                                 f'test_command = "{command}"              # Accept and merge'))
    proj.reload_policy()
    assert proj.policy.merge["test_command"] == command


class Runner:
    def __init__(self, code, output):
        self.code, self.output, self.calls = code, output, []

    def __call__(self, project, commit, command):
        self.calls.append((commit, command))
        return self.code, self.output


def test_accept_and_merge_runs_the_projects_tests_on_the_commit_it_would_land_then_merges(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj)
    runner = Runner(0, "432 passed in 150s")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", runner)
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    assert runner.calls == [(acc["data"]["commit"], "make test")]  # exactly the commit the base branch becomes
    assert out["message"] == (f"merged {tid} into {acc['data']['target']}, fast-forward: its tests passed first "
                              "(make test); nothing was pushed.")
    assert proj.task(tid)["status"] == "merged"
    [t] = kinds(proj, "merge.tested")
    assert (t["data"]["ok"], t["data"]["exit"], t["data"]["commit"]) == (True, 0, acc["data"]["commit"])


def test_a_failing_pre_merge_run_leaves_the_base_branch_and_the_card_says_what_failed(repo, monkeypatch):
    from parallax import accept as acc_mod, views
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", Runner(1, "....F\nFAILED tests/test_docs.py::test_no_placeholder - "
                                                          "AssertionError: <what>\n1 failed, 431 passed in 191s\n"))
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    [acc] = kinds(proj, "task.accepted")
    target, commit = acc["data"]["target"], acc["data"]["commit"]
    first = "FAILED tests/test_docs.py::test_no_placeholder - AssertionError: <what>"
    assert out["message"] == (f"accepted {tid} as {commit[:7]}, but its tests failed on the commit it would land, so "
                              f"{target} didn't move. first failure: {first}. fix that, then merge by hand. merge it yourself:")
    assert out["merge"] == f"git merge --ff-only {proj.task(tid)['branch']}"
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before  # the base branch didn't move
    assert proj.task(tid)["status"] == "accepted" and not kinds(proj, "merge.clicked")  # accepted, on its own branch
    card = views.card(proj, tid)
    assert card["merge_note"] == (f"Accepted, but its tests failed on the commit it would land, so {target} didn't move. "
                                  f"First failure: {first}. Fix that, then merge by hand:")
    assert card["merge"] == out["merge"]
    report = show_report(proj, tid)
    assert f"but its tests failed before the merge, so {target} didn't move" in report
    assert f"Its tests failed on that commit: {first}." in report


def test_with_no_test_command_configured_the_merge_goes_ahead_and_says_so(repo, monkeypatch):
    from parallax.ui import act
    proj, tid, wt = ready(repo)  # the default policy names none; conftest refuses any real run
    out = act(proj, "/api/accept", {"task": tid, "merge": True})
    assert "no test command is configured ([merge] test_command), so no tests ran first" in out["message"]
    assert proj.task(tid)["status"] == "merged"
    assert "No test command is configured ([merge] test_command), so it merged without a test run." in show_report(proj, tid)


def test_the_real_runner_uses_a_throwaway_worktree_at_that_commit_under_the_cap(repo):
    """Not the suite: a command that says where it ran. The worktree is gone afterwards."""
    from parallax import accept as acc_mod, sandbox
    proj, tid, wt = ready(repo)
    commit = accept(proj, tid)["data"]["commit"]
    code, output = acc_mod.run_tests(proj, commit, 'git rev-parse HEAD; echo "cap=$PARALLAX_MEMORY_CAP"; exit 3')
    assert code == 3 and output.splitlines()[:2] == [commit, f"cap={4 * 1024 ** 3}"]  # that commit, the memory cap on
    assert not (sandbox.data_home() / "merge-check" / commit[:12]).exists()
    assert "merge-check" not in git(repo, "worktree", "list").stdout
    assert acc_mod.first_failure("a\nFAILED tests/x.py::t - boom\nb\n", 1) == "FAILED tests/x.py::t - boom"
    assert acc_mod.first_failure("", 2) == "exit 2"


def show_report(proj, tid):
    from parallax import show
    return show.report(proj, tid)


# Merging is its own state: from the click until the base branch moves, or it can't (89bc50) -----------------

class Peek(Runner):
    """A test run that looks at what you'd see while it runs: the card, the board, the report."""

    def __init__(self, proj, tid, code=0, output="432 passed"):
        super().__init__(code, output)
        self.proj, self.tid, self.seen = proj, tid, {}

    def __call__(self, project, commit, command):
        from parallax import show, views
        self.seen = {"status": self.proj.task(self.tid)["status"], "card": views.card(self.proj, self.tid),
                     "report": show.report(self.proj, self.tid), "board": views.board(self.proj)}
        return super().__call__(project, commit, command)


def test_while_accept_and_merge_runs_the_task_is_merging_and_never_says_merging_is_yours(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    peek = Peek(proj, tid)
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", peek)
    act(proj, "/api/accept", {"task": tid, "merge": True})
    seen = peek.seen
    assert seen["status"] == "merging"
    card = seen["card"]
    assert card["actions"] == {"kind": "merging"} and card["merge"] == ""  # no decision, no merge command yet
    assert card["merging"]["text"] == "Merging: running the tests on the commit it would land"
    assert card["merging"]["started"] and card["chip"] == "Merging"
    assert "merging is yours" not in seen["report"].lower() and "Merging: running the tests" in seen["report"]
    [row] = [r for r in seen["board"]["working"] if r["task"] == tid]
    assert row["chip"] == "Merging" and row["line"].startswith("Merging: running the tests")
    assert row["strip"][-1] == {"stage": "ready", "name": "Merging", "state": "working", "agent": None}
    assert proj.task(tid)["status"] == "merged"  # it landed, and the card shows merged as before


def test_a_moved_base_says_it_merges_the_base_in_first(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    moved_on(repo)
    peek = Peek(proj, tid)
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", peek)
    act(proj, "/api/accept", {"task": tid, "merge": True})
    target = kinds(proj, "task.accepted")[-1]["data"]["target"]
    assert peek.seen["card"]["merging"]["text"] == f"Merging {target} in, then running the tests"


def test_a_failed_or_conflicted_merge_leaves_merging_and_shows_why_as_before(repo, monkeypatch):
    from parallax import accept as acc_mod, views
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", Runner(1, "FAILED tests/x.py::t - boom\n"))
    act(proj, "/api/accept", {"task": tid, "merge": True})
    assert proj.task(tid)["status"] == "accepted" and kinds(proj, "merge.stopped")
    card = views.card(proj, tid)
    assert card["merging"] is None and card["merge_note"].startswith("Accepted, but its tests failed")


def test_accept_and_merge_cant_be_clicked_twice(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.core import ParallaxError
    from parallax.ui import act
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    tries = []

    def again(project, commit, command):  # the second click arrives while the first is running
        for body in ({"task": tid, "merge": True}, {"task": tid}):
            with pytest.raises(ParallaxError) as err:
                act(proj, "/api/accept", body)
            tries.append(str(err.value))
        with pytest.raises(ParallaxError, match="is already merging"):
            acc_mod.merge_now(proj, tid)
        return 0, "ok"
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", again)
    act(proj, "/api/accept", {"task": tid, "merge": True})
    assert len(tries) == 2 and all("merging, not ready" in t for t in tries)
    assert len(kinds(proj, "task.accepted")) == 1 and proj.task(tid)["status"] == "merged"


def test_the_usual_duration_comes_from_past_runs(repo):
    from parallax import merging
    proj, tid, wt = ready(repo)
    for s in (100, 160, 130):
        proj.ledger.append("merge.tested", "parallax", "passed", task="x", command="scripts/test.sh", ok=True, seconds=s)
    assert merging.usual_seconds(proj) == 130 and merging.shown(130) == "2m 10s"
