"""One task that can't be shown never takes down the board, a card, or the CLI's lists (real use,
2026-09-30: the flow decision on c08f9e stored its test paths as strings under "files", where scope
decisions keep {path, cause, size}, and every /api/board failed on it)."""
import json

from parallax import decide, show, views
from parallax.cli import main
from test_accept import ready
from test_check import approved, kinds

# the stored entry, as it is in that ledger (aae0ec89), with its task id swapped for the test's
C08F9E_REASON = ("Field's test \"clicking a notification opens that task card like picking it from the list\" "
                 "(notification-click-opens-card.spec.js) still fails after a rework: expect(received).toBe(expected) "
                 "// Object.is equality. Either the test or the app is wrong")


def c08f9e_files(tid):
    return [f"docs/tasks/{tid}/ui_flows/notification-click-opens-card.spec.js",
            f"docs/tasks/{tid}/ui_flows/notify-needs-you.spec.js", f"docs/tasks/{tid}/ui_flows/notify-ready.spec.js"]


def test_a_flow_decision_stored_in_the_old_shape_displays_and_can_still_be_answered(repo, monkeypatch, capsys):
    proj, tid, _ = approved(repo)
    proj.ledger.append("disagreement.raised", "parallax", C08F9E_REASON, task=tid, stage="flows", tree="t",
                       files=c08f9e_files(tid))
    stored = [json.dumps(e, sort_keys=True) for e in proj.ledger.entries()]
    dec = decide.decision(proj, tid)
    assert dec.kind == "flows" and decide.scope_files(dec.item) == [] and decide.flow_files(dec.item) == c08f9e_files(tid)
    card = show.report(proj, tid)  # it crashed here: _files read each path as a dict
    assert card.splitlines()[1].startswith("Bottom line: Needs you: Field's test")
    row = next(i for i in views.board(proj)["waiting"] if i["task"] == tid)
    assert row["kind"] == "flows" and not row.get("broken") and row["secret"] is False
    assert views.card(proj, tid)["files"] == [] and not views.card(proj, tid).get("broken")
    removed = []
    from parallax import uitest
    monkeypatch.setattr(uitest, "remove", lambda project, task_id, files, reason: removed.extend(files))
    monkeypatch.setattr("parallax.pilot.resume", lambda *a, **k: "check")
    decide.apply(proj, tid, "remove", "the notification can't be tested in a browser")
    assert removed == c08f9e_files(tid)  # the remove option still finds its files in the old shape
    assert [json.dumps(e, sort_keys=True) for e in proj.ledger.entries()][:len(stored)] == stored  # nothing edited
    monkeypatch.chdir(repo)
    assert main(["inbox"]) == 0 and main(["task", "list"]) == 0


def test_new_flow_decisions_keep_their_test_paths_apart_from_scope_files():
    import inspect
    from parallax import check
    source = inspect.getsource(check)
    assert "Either the test or the app is wrong\", tree=s.tree, flow_files=files" in source


def test_one_broken_task_shows_as_that_and_every_other_task_displays(repo, monkeypatch, capsys):
    proj, good, _ = ready(repo)
    _, bad, _ = approved(repo)
    # deliberately broken: a scope decision whose file records have no cause or size
    proj.ledger.append("stuck.raised", "parallax", "the change goes outside the plan", task=bad, stage="scope",
                       files=[{"path": "a.txt"}, {"path": "b.txt"}])
    board = views.board(proj)
    [row] = [i for i in board["waiting"] if i["task"] == bad]
    [rec] = kinds(proj, "view.broken")  # the failure is in the ledger, like every other failure, not just the terminal
    assert rec["data"]["task"] == bad and rec["data"]["where"] == "its row" and rec["data"]["error"]
    assert rec["reason"].startswith(f"the UI couldn't display task {bad} (its row): ")
    assert row["broken"] and row["title"] == f"Task {bad}"
    assert row["line"].startswith(f"Task {bad}: couldn't display this task (ledger {rec['id']}).")  # the row cites it
    assert [i["task"] for i in board["waiting"] if not i.get("broken")] == [good]  # the rest, as usual
    assert board["waiting"][0]["task"] == bad  # first: something to look at, never silent
    err = capsys.readouterr().err
    assert f"couldn't display this task {bad} (its row)" in err and "Traceback" in err  # the reason, in the server log
    views.board(proj)  # the page polls: the same failure is recorded once, not once per refresh
    assert kinds(proj, "view.broken") == [rec]
    card = views.card(proj, bad)
    assert card["broken"] and card["report"]["bottom"].startswith(f"Task {bad}: couldn't display this task (ledger ")
    assert card["actions"] == {"kind": "none"}
    assert [e["data"]["where"] for e in kinds(proj, "view.broken")] == ["its row", "its card"]  # a different place: its own entry
    assert not views.card(proj, good).get("broken")
    before = len(proj.ledger.entries())
    monkeypatch.chdir(repo)
    assert main(["task", "list"]) == 0 and main(["inbox"]) == 0
    out = capsys.readouterr().out
    assert good in out and bad in out
    assert len(proj.ledger.entries()) >= before and kinds(proj, "stuck.raised")[-1]["data"]["files"] == [{"path": "a.txt"}, {"path": "b.txt"}]
    assert proj.task(bad)["status"] == "stuck"  # a view.broken entry changes no task's state


def test_a_board_part_that_breaks_is_recorded_without_a_task_and_the_board_still_comes_up(repo, monkeypatch, capsys):
    from parallax import progress
    proj, good, _ = ready(repo)
    tasks = proj.tasks()
    monkeypatch.setattr(progress, "overview", lambda project, now=None: (_ for _ in ()).throw(KeyError("spend")))
    board = views.board(proj)
    assert board["overview"] == [] and [i["task"] for i in board["waiting"]] == [good]  # Ready, shown as ever
    [rec] = kinds(proj, "view.broken")
    assert "task" not in rec["data"] and rec["data"]["where"] == "the overview" and rec["data"]["error"] == "KeyError: 'spend'"
    assert rec["reason"] == "the UI couldn't display the board (the overview): KeyError: 'spend'."
    assert proj.tasks() == tasks  # no task is touched by it
    assert "couldn't display this task the board (the overview)" in capsys.readouterr().err
