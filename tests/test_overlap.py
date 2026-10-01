"""Overlapping tasks get a heads-up (real use: 370571 and 40171b both added the same hint, and both
reached Ready, with nothing saying so)."""
from parallax import overlap, views
from parallax.accept import accept, merge_now
from parallax.cli import main
from parallax.inbox import items
from test_accept import ready

TITLE = "fixing the README install steps"


def test_the_newer_task_names_the_older_one_that_changes_the_same_file(repo, monkeypatch, capsys):
    proj, first, _ = ready(repo)
    _, second, _ = ready(repo)
    assert overlap.of(proj, second) == [{"task": first, "title": TITLE, "state": "Ready", "files": ["README.md"]}]
    assert overlap.of(proj, first) == []  # the older one doesn't carry it
    said = f"Heads-up: task {first} ({TITLE}), Ready, also changes README.md."
    assert views.card(proj, second)["overlaps"] == [said]
    assert views.card(proj, first)["overlaps"] == []
    [row] = [r for r in views.board(proj)["waiting"] if r["task"] == second]
    assert row["overlaps"] == [first]
    assert [i["overlaps"] for i in items(proj) if i["task"] == second] == [[said]]
    monkeypatch.chdir(repo)
    assert main(["inbox"]) == 0
    assert f"        {said}\n" in capsys.readouterr().out
    assert main(["show", second]) == 0  # the terminal's card says it too, citing where the other task's files come from
    staged = [e for e in proj.ledger.entries() if e["kind"] == "check.staged" and e["data"]["task"] == first][-1]
    out = capsys.readouterr().out
    assert f"- {said} (ledger {staged['id']})\n" in out
    assert not [f for f in views.card(proj, second)["found"] if f["text"].startswith("Heads-up")]  # shown once, above
    assert main(["show", first]) == 0 and "Heads-up" not in capsys.readouterr().out


def test_it_never_blocks_and_ends_once_the_older_task_is_merged(repo):
    proj, first, _ = ready(repo)
    _, second, _ = ready(repo)
    accept(proj, first)  # accepted but not merged: still a heads-up
    assert [o["state"] for o in overlap.of(proj, second)] == ["accepted, not merged"]
    merge_now(proj, first)
    assert proj.task(first)["status"] == "merged" and overlap.of(proj, second) == []
    assert views.card(proj, second)["actions"]["kind"] == "ready"  # nothing was ever blocked


def test_a_task_outside_the_scope_gets_no_heads_up(repo):
    proj, first, _ = ready(repo)
    _, second, _ = ready(repo)
    assert overlap.of(proj, second)
    from parallax import lifecycle
    path = lifecycle.doc_path(proj, second, "intent")
    path.write_text(path.read_text().replace("scope: README.md, tests/**,", "scope: tests/**,"))
    assert overlap.of(proj, second) == []
