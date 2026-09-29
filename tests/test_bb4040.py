"""Live in bb4040: the sandbox's empty placeholders reached the plan check, and the card led with .env."""
from pathlib import Path

import pytest

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, junit_runner
from parallax import build, lint, pilot, sandbox, show, tree
from parallax.core import Project
from test_m8 import WANT, docs, make_key

BB4040 = [".env", ".env.development", ".env.development.local", ".env.local", ".env.production",
          ".env.production.local", ".env.test", ".env.test.local", ".gitmodules", ".npmrc", ".yarnrc",
          ".yarnrc.yml", "bunfig.toml", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock"]


@pytest.fixture
def proj(repo, monkeypatch):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda *a: 1)
    return Project.init(repo)


def kinds(proj, kind):
    return [e for e in proj.ledger.entries() if e["kind"] == kind]


def run(proj, tid, maker, probe):
    return build.run_mode(proj, tid, "pilot", FakeDrafter(docs()), lambda left, settings: maker, FakeChecker(),
                          test_runner=junit_runner(), preflight_runner=probe)


def test_placeholders_left_before_the_build_never_reach_the_plan_check(proj):
    """The window bb4040 fell into: after the drafters' cleanup, before the build's own snapshot."""
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])

    def probe(config, cwd, spec, env):  # preflight runs between drafting and the build
        for name in BB4040:
            (wt / name).touch()
        return {"written": [], "readable": [], "network": [], "env": ["HOME", "PATH"], "env_values": []}

    maker = ScriptedAgent(steps=[("write", "README.md", "ok\n")])
    assert run(proj, tid, maker, probe) == "ready"  # the build's guard runs parallax diff, which marks them intent-to-add
    staged = kinds(proj, "check.staged")[-1]["data"]
    assert staged["problems"] == [] and set(staged["files"]) <= {"README.md", "tests/test_readme.py"}
    assert not any((wt / n).exists() for n in BB4040)
    assert set(kinds(proj, "sandbox.cleaned")[-1]["data"]["files"]) == set(BB4040)
    assert not proj.inbox()


def test_a_placeholder_name_with_content_is_real_work_and_stays(proj):
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])
    (wt / ".env").write_text("TOKEN=abc\n")
    (wt / ".gitmodules").touch()
    assert sandbox.remove_leftovers(wt, {".gitmodules"}) == []  # the check leaves the plan's own files
    assert (wt / ".env").read_text() == "TOKEN=abc\n" and (wt / ".gitmodules").exists()


def staged_with(wt, files):
    s = tree.Staged(tree="t", base="b", diff="", files=list(files), lines=1)
    return tree.conform(s, {"files": ["README.md"], "binaries": [], "symlinks": [], "dependencies": []}, 400)


def test_many_findings_with_one_cause_read_as_one_line(tmp_path):
    for name in BB4040:
        (tmp_path / name).touch()
    why, files = tree.describe(staged_with(tmp_path, BB4040), tmp_path)
    assert why.startswith("17 files changed outside the plan's files, all empty (.env, .env.development and 15 more)")
    assert "dependencies" not in why and len(files) == 17 and {f["size"] for f in files} == {0}


@pytest.mark.parametrize("content, says", [
    ("", ".env changed but isn't in the plan's files, and it is empty"),
    ("SECRET=1\n", ".env looks like a secrets file and has content (9 bytes); .env changed but isn't in the plan's files"),
])
def test_a_secret_path_says_whether_it_has_content(tmp_path, content, says):
    (tmp_path / ".env").write_text(content)
    assert tree.describe(staged_with(tmp_path, [".env"]), tmp_path)[0] == says


def test_the_card_counts_and_lists_the_rest_under_details(proj):
    tid = pilot.intake(proj, WANT)["task"]
    wt = Path(proj.task(tid)["worktree"])
    for name in BB4040:
        (wt / name).touch()
    why, files = tree.describe(staged_with(wt, BB4040), wt)
    proj.ledger.append("disagreement.raised", "parallax", why, task=tid, stage="scope", files=files)
    text = show.report(proj, tid)
    ids = {e["id"] for e in proj.ledger.entries()}
    assert lint.lint_report(text, root=proj.root, ledger_ids=ids) == []
    head, details = text.split("\nDetails\n", 1) if "\nDetails\n" in text else (text, "")
    assert "Bottom line: Needs you: 17 files changed outside the plan's files, all empty" in head
    assert "yarn.lock" not in head and all(f"{n}: not in the plan, empty" in details for n in BB4040)

