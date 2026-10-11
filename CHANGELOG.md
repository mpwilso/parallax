# Changelog

What changed in each release. The full notes for each are on the [releases page](https://github.com/mpwilso/parallax/releases).

## Unreleased

Fixed:
- A ledger line torn by a crash mid-write no longer breaks the ledger. The next entry used to join it, making a line no read could parse again; the torn tail now moves, byte for byte, to `.parallax/ledger.jsonl.torn` before the next append. No whole entry is touched.
- A `[build] setup` that made the venv and then failed left it behind, and the retry used it without running setup again. A failed setup now removes a venv it made.
- A venv left by Parallax being killed mid-setup was taken for ready. A venv now counts as ready only with the install record that a successful setup writes; without it, setup runs again.
- `[build] setup` had no time limit, so a stalled install hung the build. It now stops after 30 minutes, the same as a test run, and fails like any other setup.
- `parallax doctor` finds the project from a subfolder, and no longer leaves a ledger in a folder outside a project.
- A sandbox that didn't start: the card quotes what bwrap or srt printed, and gives one next step.
- The suite passes inside a Maker's sandbox: tests ignore its temp folder.

Added:
- `parallax doctor` checks the repo it runs in (a commit, and a branch checked out), and that the tests' python has pytest when there's no `[build] setup`.

Also: ruff is pinned in `scripts/test.sh`. CI runs once per push, and again for a pull request only when it comes from a fork; Dependabot proposes action updates a week after their release. The README names Parallax as the second of three tools, with Loupe and ISR.

## 0.2.1 (2026-10-02)

A patch release.

Fixed:
- Two tasks could draw the same task ID. A new ID is now checked against the ledger, branches, worktrees and task folders before it's used.
- When a git command fails, the error shows git's own message instead of its first progress line.
- In a repo with no spending mode chosen yet, the page printed 'null' under the first-run spending question.

Also: the README's demo and Ready card images show the current UI.

## 0.2.0 (2026-10-01)

Adds a second checker that writes tests of what you asked before anything is built, makes the app usable for everyday work, and measures the checkers against real merged fixes. Still a prototype.

- Reticle, a new agent. Before the build, it writes tests of the outcomes you asked for. Maker can't see or change them.
- Second Eye fails a change only on what you asked for: a blocking finding has to cite an outcome you asked for or a constraint, or it becomes a note.
- Safer accept and merge. Accept and merge runs your project's tests on the exact commit it would land, and works when the base branch has moved.
- The app, for everyday use: a queue with a stage strip per task, an Ask box, notifications, light and dark mode, and stop at any stage, with every stop naming the next action.
- Spending you set once: a budget mode, a floor on each plan's cap from real spend, and one question to allow more when a cap stops a task.
- Fewer surprises: memory caps on sandbox commands and test runs, missing tools caught before a task starts, a heads-up for overlapping tasks, and cleanup.
- `parallax eval` runs real merged fixes from small open-source Python projects through the pipeline and scores them with the maintainers' hidden tests, with a seeded mode that breaks each fix by code.

Known limits: results vary from run to run, a requirements gap gets past every checker, seeded versions are mechanical edits, and only small pure-Python projects have been tested.

## 0.1.0 (2026-09-30)

First public version. Parallax runs Claude Code agents on your repo inside a sandbox, checks their work with a blind reviewer and your tests, and brings you one decision per task. A working prototype.
