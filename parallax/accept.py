"""`parallax accept <task>`: commit exactly what was reviewed. Parallax never merges without your click.

Accept:
- only at Ready;
- re-checks every approved lifecycle file against its recorded hash (a mismatch blocks accept);
- runs the secrets scan over the change; a hit blocks unless you accept the risk, with a reason;
- writes docs/tasks/<id>/record.md from the ledger;
- commits exactly the reviewed tree plus docs/tasks/<id>/, on the task's base, with git plumbing
  (commit-tree runs no hooks), and trailers. It's signed if you've set a signing key;
- points the task's branch at that commit, moves the untracked docs/tasks/<id>/ out of your
  checkout (git won't merge over it; the merge brings it back), and prints the merge command.
Parallax never merges without your click: the card's Accept and merge accepts, then fast-forwards
the base branch locally (merge_now), and stops with one line if it can't. It never pushes, and
nothing forces. Your next command checks whether the commit landed unchanged.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import lifecycle, lint, memcap, record, sandbox, secretscan, tree
from .core import ParallaxError, Project, refuse_inside_task


def _git(root: Path, *args: str, env: dict | None = None, input: str | None = None) -> str:
    out = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, env=env, input=input)
    if out.returncode != 0:
        raise ParallaxError(f"git {args[0]} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def _last(project: Project, task_id: str, kind: str) -> dict | None:
    hits = [e for e in project.ledger.entries() if e["kind"] == kind and e["data"].get("task") == task_id]
    return hits[-1] if hits else None


def signing_key(root: Path) -> str:
    out = subprocess.run(["git", "-C", str(root), "config", "--get", "user.signingkey"], capture_output=True, text=True)
    return out.stdout.strip()


def message(project: Project, task_id: str, head: str) -> str:
    intent = lifecycle.doc_path(project, task_id, "intent").read_text(encoding="utf-8")
    title = (lint.intent_fields(intent).get("title") or project.task(task_id)["goal"]).strip().rstrip(".")
    subject = (title[:1].upper() + title[1:])[:72]
    who = record.approver()
    approved = [f"Approved-By: {who} ({g['data']['gate']}) {record.stamp(g['ts'])}"
                for g in lifecycle.state(project, task_id).approved]
    tests, verdict = _last(project, task_id, "tests.recorded"), None
    verdicts = [e for e in project.ledger.entries()
                if e["kind"] == "verdict.recorded" and e["data"].get("task") == task_id and e["data"].get("stage") == "check"]
    verdict = verdicts[-1]["data"]["verdict"] if verdicts else "none"
    passed = f"{tests['data']['passed']}/{tests['data']['total']}" if tests else "none"
    trailers = [f"Parallax-Task: {task_id}", f"Intent: docs/tasks/{task_id}/intent.md", *approved,
                f"Verified-By: checker={verdict} tests={passed}", f"Ledger-Head: {head}"]
    return f"{subject}\n\nRecord: docs/tasks/{task_id}/record.md\n\n" + "\n".join(trailers) + "\n"


def accept(project: Project, task_id: str, reason: str = "") -> dict:
    refuse_inside_task(project.root)
    t = lifecycle.lifecycle_task(project, task_id)
    if t["status"] != "ready":
        raise ParallaxError(f"task {task_id} is {t['status']}, not ready. parallax show {task_id} says why")
    st = lifecycle.state(project, task_id)
    for e in st.approved:
        for doc, sha in e["data"]["files"].items():
            path = lifecycle.doc_path(project, task_id, doc)
            if not path.exists() or lifecycle.file_hash(path) != sha:
                raise ParallaxError(f"{lifecycle.rel(project, path)} doesn't match the version you approved. "
                                    "put it back as it was, or reject the task")
    staged = _last(project, task_id, "check.staged")
    if staged is None:
        raise ParallaxError(f"task {task_id} has no reviewed tree")
    reviewed, base, wt = staged["data"]["tree"], t["base"], Path(t["worktree"])

    diff = _git(wt, "diff", "--binary", base, reviewed)
    hits = secretscan.scan_diff(diff)
    changed = tree.changed_between(wt, base, reviewed)
    leaks, note = secretscan.gitleaks({f: tree.show_file(wt, reviewed, f) for f in changed
                                       if f in tree.files_in(wt, reviewed)})
    hits += leaks
    project.ledger.append("secrets.scanned", "parallax", note, task=task_id, tree=reviewed, hits=hits,
                          gitleaks=not note)
    if hits and not reason.strip():
        raise ParallaxError("the secrets scan found: " + "; ".join(hits)
                            + ". if it's not a secret, accept with --reason to take the risk")
    if hits:
        project.ledger.append("risk.accepted", "human", reason, task=task_id, hits=hits)

    plan = lifecycle.plan_data(project, task_id)
    rec = lifecycle.doc_path(project, task_id, "record")
    rec.write_text(record.write(project, task_id, changed, plan), encoding="utf-8")

    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(project.root, "read-tree", reviewed, env=env)
        folder = lifecycle.task_dir(project, task_id)
        for f in sorted(folder.iterdir()):
            if f.is_file() and f.suffix == ".md":
                blob = _git(project.root, "hash-object", "-w", str(f))
                _git(project.root, "update-index", "--add", "--cacheinfo",
                     f"100644,{blob},docs/tasks/{task_id}/{f.name}", env=env)
        committed_tree = _git(project.root, "write-tree", env=env)
    head = project.ledger.entries()[-1]["hash"]
    msg = message(project, task_id, head)
    key = signing_key(project.root)
    commit = _git(project.root, "commit-tree", committed_tree, "-p", base, *(["-S"] if key else []), "-F", "-",
                  input=msg)
    branch = t["branch"]
    _git(project.root, "update-ref", f"refs/heads/{branch}", commit)
    subprocess.run(["git", "-C", str(wt), "reset", "-q"], capture_output=True)  # the worktree's index follows

    # the commit now holds docs/tasks/<id>/. git won't merge over untracked copies of the same files,
    # even identical ones, so they move out of your checkout; the merge brings them back, tracked
    kept = sandbox.task_home(project.root, task_id) / "docs-at-accept"
    if kept.exists():
        shutil.rmtree(kept)
    shutil.move(str(lifecycle.task_dir(project, task_id)), str(kept))

    target = subprocess.run(["git", "-C", str(project.root), "symbolic-ref", "--short", "-q", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    ff = _git(project.root, "rev-parse", "HEAD") == base
    return project.ledger.append("task.accepted", "human", reason, task=task_id, commit=commit, tree=committed_tree,
                                 reviewed=reviewed, branch=branch, target=target, ff=ff, signed=bool(key),
                                 ledger_head=head, docs_moved_to=str(kept))


TEST_TIMEOUT = 3600  # seconds the project's tests may take before the merge gives up on them


def run_tests(project: Project, commit: str, command: str) -> tuple[int, str]:
    """The project's test command on exactly this commit: a throwaway worktree outside the repo, under
    the memory cap. Returns (exit code, output). The worktree is removed whatever happens."""
    folder = sandbox.data_home() / "merge-check" / commit[:12]
    if folder.exists():
        subprocess.run(["git", "-C", str(project.root), "worktree", "remove", "--force", str(folder)], capture_output=True)
        shutil.rmtree(folder, ignore_errors=True)
    folder.parent.mkdir(parents=True, exist_ok=True)
    _git(project.root, "worktree", "add", "--detach", str(folder), commit)
    try:
        out = memcap.run(["bash", "-c", command], memcap.SUITE, what="the tests", timeout=TEST_TIMEOUT, capture=True, cwd=folder)
        return out.code, out.output if not out.timed_out else out.output + f"\nthe tests ran past {TEST_TIMEOUT // 60} minutes"
    finally:
        subprocess.run(["git", "-C", str(project.root), "worktree", "remove", "--force", str(folder)], capture_output=True)


TEST_RUNNER = run_tests  # tests replace this; they never run a real suite


def first_failure(output: str, code: int) -> str:
    """The line a person needs first: pytest's first FAILED or ERROR line, else the last line."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    hit = next((line for line in lines if line.startswith(("FAILED ", "ERROR "))), None)
    return " ".join((hit or (lines[-1] if lines else f"exit {code}")).split())[:240]


def merge_now(project: Project, task_id: str, runner=None) -> str:
    """Your click on Accept and merge, after accept: fast-forward the branch the task was based on to
    the accepted commit, locally, and only fast-forward. First, if the policy names the project's
    test command ([merge] test_command), it runs on exactly that commit, and a failure stops here.
    Raises with one line saying why it can't: then the base branch hasn't moved, and the merge
    command is still yours to run. Never pushes."""
    acc = _last(project, task_id, "task.accepted")
    if acc is None:
        raise ParallaxError(f"task {task_id} isn't accepted yet")
    d = acc["data"]
    on = subprocess.run(["git", "-C", str(project.root), "symbolic-ref", "--short", "-q", "HEAD"],
                        capture_output=True, text=True).stdout.strip()
    if not d.get("target") or on != d["target"]:
        raise ParallaxError(f"can't merge from here: your checkout is on {on or 'no branch'}, not {d.get('target') or 'a branch'}")
    if subprocess.run(["git", "-C", str(project.root), "merge-base", "--is-ancestor", "HEAD", d["commit"]],
                      capture_output=True).returncode != 0:
        raise ParallaxError(f"can't fast-forward {d['target']} to it: {d['target']} has moved on since the task began")
    command = project.policy.merge["test_command"].strip()
    if command:  # the project's own tests, on the exact commit the base branch would become
        code, output = (runner or TEST_RUNNER)(project, d["commit"], command)
        first = first_failure(output, code) if code else ""
        project.ledger.append("merge.tested", "parallax", first or "passed", task=task_id, commit=d["commit"],
                              command=command, exit=code, ok=code == 0)
        if code:
            raise ParallaxError(f"its tests failed on the commit it would land, so {d['target']} didn't move. "
                                f"first failure: {first}. fix that, then merge by hand")
    else:
        project.ledger.append("merge.tested", "parallax", "no test command is configured", task=task_id, commit=d["commit"],
                              command="", exit=None, ok=None)
    out = subprocess.run(["git", "-C", str(project.root), "merge", "--ff-only", "-q", d["branch"]], capture_output=True, text=True)
    if out.returncode != 0:
        why = next((line.strip() for line in (out.stderr or out.stdout).splitlines() if line.strip()), "git refused")
        raise ParallaxError(f"can't fast-forward {d['target']} to it: {why.removeprefix('fatal: ').rstrip('.')}")
    project.ledger.append("merge.clicked", "human", "Accept and merge: fast-forward, local, nothing pushed", task=task_id,
                          commit=d["commit"], target=d["target"])
    confirm_merges(project)
    ran = f"its tests passed first ({command}); " if command else \
        "no test command is configured ([merge] test_command), so no tests ran first; "
    return f"merged {task_id} into {d['target']}, fast-forward: {ran}nothing was pushed."


def merge_command(accepted: dict) -> str:
    d = accepted["data"]
    return f"git merge {'--ff-only ' if d['ff'] else ''}{d['branch']}"


def confirm_merges(project: Project) -> list[str]:
    """Housekeeping on every command: did you merge an accepted commit, unchanged?"""
    confirmed = {e["data"]["task"] for e in project.ledger.entries() if e["kind"] == "merge.confirmed"}
    out = []
    for e in project.ledger.entries():
        d = e["data"]
        if e["kind"] != "task.accepted" or d["task"] in confirmed:
            continue
        ref = f"refs/heads/{d['target']}" if d.get("target") else "HEAD"
        head = subprocess.run(["git", "-C", str(project.root), "rev-parse", "-q", "--verify", ref],
                              capture_output=True, text=True).stdout.strip()
        if head and subprocess.run(["git", "-C", str(project.root), "merge-base", "--is-ancestor", d["commit"], head],
                                   capture_output=True).returncode == 0:
            project.ledger.append("merge.confirmed", "parallax", "the accepted commit is in the branch unchanged",
                                  task=d["task"], commit=d["commit"], target=d.get("target"), head=head)
            confirmed.add(d["task"])
            out.append(d["task"])
    return out
