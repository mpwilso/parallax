"""Parallax cleans up after itself (real use: 7 finished and dropped tasks left their worktrees and
branches behind, and drafts sat untracked in docs/tasks/ of the checkout)."""
import hashlib
import subprocess
from pathlib import Path

import pytest

from parallax import decide, drafts, lifecycle, show, views
from parallax.accept import accept, confirm_merges
from parallax.ui import act
from test_accept import git, ready


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch):
    from parallax import build
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)


def cleaned(proj, tid):
    return [e for e in proj.ledger.entries() if e["kind"] == "task.cleaned" and e["data"]["task"] == tid]


def branch_exists(repo, branch):
    return git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{branch}").returncode == 0


def test_drafts_never_sit_in_your_checkout_and_accept_still_commits_them(repo):
    proj, tid, wt = ready(repo)
    assert not (repo / "docs" / "tasks").exists()
    assert "docs/tasks" not in git(repo, "status", "--porcelain", "--untracked-files=all").stdout
    assert not lifecycle.task_dir(proj, tid).is_relative_to(repo)
    acc = accept(proj, tid)["data"]
    committed = git(repo, "ls-tree", "-r", "--name-only", acc["commit"], f"docs/tasks/{tid}/").stdout.split()
    assert committed == [f"docs/tasks/{tid}/intent.md", f"docs/tasks/{tid}/plan.md", f"docs/tasks/{tid}/record.md"]
    record = git(repo, "show", f"{acc['commit']}:docs/tasks/{tid}/record.md").stdout
    assert record == (lifecycle.task_dir(proj, tid) / "record.md").read_text()  # what another tool reads, as before


def test_a_merged_task_loses_its_worktree_and_branch_and_its_card_still_shows(repo):
    proj, tid, wt = ready(repo)
    branch = proj.task(tid)["branch"]
    act(proj, "/api/accept", {"task": tid, "merge": True})
    assert proj.task(tid)["status"] == "merged"
    [c] = cleaned(proj, tid)
    assert c["data"]["worktree_removed"] and c["data"]["branch_deleted"]
    assert not wt.exists() and not branch_exists(repo, branch)
    assert str(wt) not in git(repo, "worktree", "list").stdout
    card = show.report(proj, tid)
    assert "you merged it unchanged" in card
    assert not views.card(proj, tid).get("broken")
    assert "+ok" in views.document(proj, tid, "diff")  # from the commits, with the worktree gone
    assert views.document(proj, tid, "intent").startswith("Bottom line:")


def test_a_merge_you_run_yourself_is_cleaned_up_by_your_next_command(repo):
    proj, tid, wt = ready(repo)
    branch = proj.task(tid)["branch"]
    acc = accept(proj, tid)["data"]
    assert wt.exists() and branch_exists(repo, branch)  # accepted, not merged: both still there
    git(repo, "merge", "--ff-only", "-q", branch)
    assert confirm_merges(proj) == [tid]
    assert not wt.exists() and not branch_exists(repo, branch)
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == acc["commit"]  # the work is in your branch


def test_a_branch_with_commits_not_in_the_target_is_kept(repo):
    proj, tid, wt = ready(repo)
    branch = proj.task(tid)["branch"]
    accept(proj, tid)
    git(repo, "merge", "--ff-only", "-q", branch)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "yours, after"], check=True)
    git(repo, "branch", "-f", branch, git(wt, "rev-parse", "HEAD").stdout.strip())
    confirm_merges(proj)
    [c] = cleaned(proj, tid)
    assert not c["data"]["branch_deleted"] and branch_exists(repo, branch) and "kept" in c["reason"]


def test_a_dropped_task_keeps_its_work_as_a_patch_then_loses_its_worktree_and_branch(repo):
    proj, tid, wt = ready(repo)
    (wt / "notes.txt").write_text("half done\n")  # work never committed, untracked included
    branch = proj.task(tid)["branch"]
    proj.ledger.append("stuck.raised", "parallax", "it stopped", task=tid)
    assert decide.apply(proj, tid, "drop", "not needed after all") == f"dropped {tid}. it's out of the inbox."
    [c] = cleaned(proj, tid)
    patch = Path(c["data"]["patch"])
    assert patch.read_bytes() and hashlib.sha256(patch.read_bytes()).hexdigest() == c["data"]["patch_sha"]
    assert not patch.is_relative_to(repo) and oct(patch.stat().st_mode & 0o777) == "0o600"
    assert {"README.md", "notes.txt"} <= set(c["data"]["files"])
    assert not wt.exists() and not branch_exists(repo, branch)
    # the patch is the work: it applies to the base and gives it back
    check = repo.parent / "apply-check"
    subprocess.run(["git", "clone", "-q", str(repo), str(check)], check=True)
    subprocess.run(["git", "-C", str(check), "checkout", "-q", proj.task(tid)["base"]], check=True)
    subprocess.run(["git", "-C", str(check), "apply", str(patch)], check=True)
    assert (check / "notes.txt").read_text() == "half done\n"
    assert not views.card(proj, tid).get("broken") and "rejected" in show.report(proj, tid)


def test_an_older_tasks_drafts_in_your_checkout_still_work_and_leave_with_the_drop(repo):
    proj, tid, wt = ready(repo)
    new = lifecycle.task_dir(proj, tid)
    old = drafts.legacy(repo, tid)
    old.parent.mkdir(parents=True)
    new.rename(old)  # as tasks begun before this kept them
    assert lifecycle.task_dir(proj, tid) == old and lifecycle.found_doc(proj, tid, "intent") == old / "intent.md"
    proj.ledger.append("stuck.raised", "parallax", "it stopped", task=tid)
    decide.apply(proj, tid, "drop", "not needed")
    [c] = cleaned(proj, tid)
    assert not (repo / "docs" / "tasks").exists() and Path(c["data"]["drafts_moved_to"]) == drafts.folder(repo, tid)
    assert (drafts.folder(repo, tid) / "intent.md").exists()


def test_a_task_dropped_while_it_runs_keeps_its_worktree(repo):
    proj, tid, wt = ready(repo)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=1, mode="check")  # pid 1 is always alive
    proj.ledger.append("stuck.raised", "parallax", "it stopped", task=tid)
    decide.apply(proj, tid, "drop", "not needed")
    [c] = cleaned(proj, tid)
    assert wt.exists() and not c["data"]["worktree_removed"] and "still running" in c["reason"]


def test_tasks_merged_or_dropped_before_this_are_left_alone(repo):
    proj, tid, wt = ready(repo)
    proj.ledger.append("task.rejected", "human", "dropped before cleanup existed", task=tid, was="ready")
    confirm_merges(proj)
    assert wt.exists() and not cleaned(proj, tid)
