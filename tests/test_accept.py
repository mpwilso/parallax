import os
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
    hook.write_text(f"#!/bin/sh\ntouch {repo}/HOOK-RAN\n")
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
                 "signed with the approval key", "Written by: the maker (claude-opus-5), in 1 run in the sandbox.",
                 "Verified by: Parallax ran the plan's tests in the sandbox (3 of 3 passed)",
                 f"Rollback: revert the commit whose message has Parallax-Task: {tid}",
                 "Known risks (agent-written, from the checker): README.md:1 minor: could say which shell",
                 "Not looked at: the checker says: the rendered page."):
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
