"""A failing check keeps its whole output, as a file whose hash is in the ledger (real use: only the
last lines reached the ledger, and the cause was above them)."""
import hashlib
from pathlib import Path

import pytest

from fakes import FakeChecker, junit_runner
from parallax import ask, outputs, views
from test_check import approved, built, kinds, run

FULL = "FIRST LINE OF THE RUN: the real cause\n" + "\n".join(f"line {i}" for i in range(200)) + "\nE   AssertionError: boom\n1 failed\n"


def noisy(results=None, exit_code=1):
    """The plan's tests, failing, with a long output: the cause is far above the last lines."""
    base = junit_runner(results, exit_code)

    def runner(config, cwd, cmd, env):
        code, _ = base(config, cwd, cmd, env)
        return code, FULL
    return runner


def failed_once(repo):
    proj, tid, wt = approved(repo)
    maker = built(proj, tid, [("write", "README.md", "ok\n")])
    runs = iter([noisy({"tests/test_readme.py": (2, 1)}), junit_runner()])
    assert run(proj, tid, maker, runner=lambda *a: next(runs)(*a)) == "ready"
    return proj, tid


def test_a_failing_check_keeps_its_whole_output_and_the_ledger_its_hash(repo):
    proj, tid = failed_once(repo)
    failing, passing = kinds(proj, "tests.recorded")
    d = failing["data"]
    assert "FIRST LINE OF THE RUN" not in failing["reason"]  # the ledger's reason is still the last lines
    path = Path(d["output"])
    assert path.read_text() == FULL and d["output_sha"] == hashlib.sha256(FULL.encode()).hexdigest()
    assert "check-output" in path.parts and not path.is_relative_to(proj.root)  # the task's data folder, never the repo
    assert "output" not in passing["data"]  # a passing run keeps only its last lines, as before
    assert outputs.read(failing) == FULL
    assert proj.ledger.verify()[0]


def test_a_changed_output_file_is_refused_not_shown(repo):
    proj, tid = failed_once(repo)
    failing = kinds(proj, "tests.recorded")[0]
    Path(failing["data"]["output"]).write_text("all passed, honest\n")
    with pytest.raises(outputs.Tampered, match="its hash doesn't match"):
        outputs.read(failing)
    with pytest.raises(ValueError, match="changed after it was recorded"):
        views.output(proj, tid, failing["id"])
    assert "not shown: the full output of ledger entry" in ask.record(proj, tid)


def test_the_card_links_the_failure_line_to_the_full_output(repo):
    proj, tid, wt = approved(repo)
    (wt / "tests").mkdir()
    maker = built(proj, tid, [("write", "README.md", "ok\n"), ("write", "tests/test_readme.py", "def test(): pass\n")])
    assert run(proj, tid, maker, FakeChecker(), runner=noisy(exit_code=4)) == "disputed"
    [item] = proj.inbox()
    assert item["data"]["output_sha"]  # the stop itself carries it: its line on the card links to it
    card = views.card(proj, tid)
    line = next(f for f in card["found"] if f["cite"] == f"ledger {item['id']}")
    assert line["text"].startswith("the plan's tests couldn't run (exit 4)") and line["output"] == item["id"]
    assert views.output(proj, tid, item["id"]) == FULL
    with pytest.raises(ValueError):
        views.output(proj, tid, "nope")


def test_the_ask_box_reads_the_full_output(repo):
    proj, tid = failed_once(repo)
    record = ask.record(proj, tid)
    failing = kinds(proj, "tests.recorded")[0]
    assert "## test output" in record and "FIRST LINE OF THE RUN: the real cause" in record
    assert f"[ledger {failing['id']}] the whole output of tests.recorded" in record
    assert "[test output]" in ask.PROMPT


def test_a_failing_merge_test_run_keeps_its_whole_output(repo, monkeypatch):
    from parallax import accept as acc_mod
    from parallax.core import ParallaxError
    from test_accept import Runner, _with_merge_tests, ready
    proj, tid, wt = ready(repo)
    _with_merge_tests(repo, proj, "scripts/test.sh")
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", Runner(1, FULL))
    acc_mod.accept(proj, tid, merging=True)
    with pytest.raises(ParallaxError):
        acc_mod.merge_now(proj, tid)
    [tested] = kinds(proj, "merge.tested")
    assert outputs.read(tested) == FULL
    line = next(f for f in views.card(proj, tid)["found"] if f["cite"] == f"ledger {tested['id']}")
    assert line["output"] == tested["id"]


def test_the_page_gets_the_full_output_only_while_its_hash_matches(repo):
    import threading
    from parallax.ui import UI
    from test_ui_server import call
    proj, tid = failed_once(repo)
    failing = kinds(proj, "tests.recorded")[0]
    app = UI(proj.root, port=0, find=lambda tool: f"/usr/bin/{tool}")
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    try:
        code, body, _ = call(app, "GET", f"/api/task/{tid}/output/{failing['id']}")
        assert code == 200 and body["text"] == FULL
        Path(failing["data"]["output"]).write_text("edited\n")
        code, body, _ = call(app, "GET", f"/api/task/{tid}/output/{failing['id']}")
        assert code == 400 and "hash doesn't match" in body["error"]
    finally:
        app.close()
