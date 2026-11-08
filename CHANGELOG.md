# Changelog

What changed in each release. The full notes for each are on the [releases page](https://github.com/mpwilso/parallax/releases).

## 0.7.2 (2026-11-08)

Fixed:
- Task venvs are built on the system Python. Field-demo task 70d1f6's tests stopped with exit 127, `python: command not found`: its setup's `uv venv` took uv's own Python, so the venv's `bin/python` linked into `~/.local/share/uv/python`, under your home folder, which the tests' sandbox can't read (it lets in only the check copy, the venv, the tests' temp folder and Field's tools). Maker had worked around the same thing with `/usr/bin/python3`. `[build] setup` now runs with uv held to a system Python (`UV_PYTHON_PREFERENCE=only-system`, no downloads), so the same `uv venv "$PARALLAX_VENV"` makes the venv on `/usr/bin/python3`, and Parallax's own setup lines say `uv venv --python /usr/bin/python3`. What the sandbox may read is unchanged. Before a Python setup runs, a missing `/usr/bin/python3`, or one older than the project's `requires-python`, stops the task with what to install. After it, a venv whose Python the sandbox can't read is removed and the task stops saying so, never falling back to uv's Python. A finished venv like 70d1f6's is made again on the next run, so a retry picks up the new one. CI installs a uv-managed Python first, so the new test has one to avoid. Tests: `test_the_venvs_python_resolves_inside_what_the_tests_sandbox_can_read`, which failed before the fix in WSL with the venv on uv's Python 3.13, and `test_a_finished_venv_on_a_python_the_sandbox_cant_read_is_made_again`.
- When the task stopped for Polarizer's approval, the card asked the generic "The plan's tests couldn't run: try again once the cause is fixed?". It now asks its own question, recommends retry, and gives the next step: run the `polarizer approve` command it shows in a terminal of your own, then `parallax decide <task> retry`. Test: `test_before_anything_is_approved_the_task_stops_for_you_before_field_spends`.
- That card said Polarizer was keeping "19 of Field's browser tools" back, where `polarizer pending` and its group held 21. It now counts what Polarizer lists, and says which are Field's: "the 19 Field may use (...) and 2 it never gets, which Parallax refuses before they reach Polarizer".
- The `uitest.polarizer` entry's line in `parallax log` was empty when nothing stopped the run. It now gives the counts, such as `Polarizer: 14 calls, 0 refused, 0 holds`, and still names the problem when there is one.
- docs/using.md says `polarizer approve` must be run by a person in a real terminal, since it refuses without one, and gives the exact command the card shows.
- Field's route is pinned to Polarizer 0.6.2, released today (its card wraps long lines; nothing Parallax reads changed): the install line in its error and in docs/using.md, and `scripts/test.sh`, whose real-Polarizer test runs against 0.6.2.

## 0.7.1 (2026-11-08)

Fixed:
- Field through Polarizer couldn't find a `polarizer` installed the documented way. `uv tool install` puts it in `~/.local/bin`, which your shells and `parallax ui` find, but the builder runs with a plain PATH (the task's venv, the system folders and the sandbox tools' folders), so field-demo task 70d1f6 stopped at check with "polarizer isn't on PATH". Launch now looks it up where you run Parallax, on your PATH and then in `~/.local/bin` as `parallax doctor` does, and hands the builder its absolute path. The builder's PATH is unchanged, a relative PATH entry never counts, and no policy can name the program. `parallax doctor` and `parallax ui` now name a missing polarizer, with its install line, when `[ui_tester]` turns the route on, and docs/using.md's check says so instead of `command -v polarizer`. Test: `test_the_builder_finds_the_polarizer_you_installed_though_its_path_is_plain`.
- Field's route is pinned to Polarizer 0.6.1, which fixes 0.6.0's crash on deeply nested ledger lines: the install line in its error and in docs/using.md, and `scripts/test.sh`, whose real-Polarizer test now runs against 0.6.1.
- A raised cap called its whole reserve Field's. The card said "Field's $1.00" where `[ui_tester] max_usd` is $0.50. The $1.00 was right: the cap keeps each one's limit for Field and for Reticle, since both spend from it. But all of it was called Field's, even when Field wasn't going to run. The card now names each share, such as "Field's $0.50 and Reticle's $0.50", and so does a misfit that asks for room for a rework. Test: `test_the_raised_cap_names_each_share_it_keeps`.

## 0.7.0 (2026-11-08)

- One trust question for a whole eval run. Each eval case is a scratch repo with a new ledger and its own `[build] setup`, so since policy trust (2026-10-23) every case stopped at the trust question with nothing to answer it, and the 2026-11-07 rerun wasn't started. `parallax eval` now asks once, at a terminal, before any case starts: the question lists every case's commands word for word, read back from the scratch policy it will write, with the run's budget. A yes is recorded as yours (`eval.trusted`) and signed with the approval key; as each case starts, code carries it into that case's scratch ledger for that case's commands only (`policy.trusted.run`) and ends it when the case's pipeline ends (`policy.trust.ended`), and it runs out on its own after the case's time limit. Nothing is trusted before or after, and a command not on the list still asks. A no takes a reason, is recorded (`eval.refused`), and runs no case. A pipe or a Claude Code shell can't answer, so nothing runs. `trust.trusted` now also honours a run's trust, through `trust.for_run`; every other trust is unchanged (`parallax/evaltrust.py`). Tests: `tests/test_eval_trust.py`, including the problem reproduced: without the run's trust, a case stops at the question with nothing built.

Fixed:
- A Field ledger Polarizer won't serve on no longer lets Field spend. Parallax read a torn tail as fine and any other bad verdict as a reason to skip the listing pass, but `polarizer serve` refuses every ledger that isn't intact, a torn tail included, so Field's session ran with no browser tools and spent money before the run's read stopped it. The ledger is now verified before every run, under Polarizer's lock and with its `ledger.head`, and anything but intact v1 stops the task before Field starts: a torn tail with `polarizer repair`, anything else as a person's job, since repair refuses it. Test: `test_a_ledger_polarizer_wont_serve_on_stops_the_task_before_field_spends`.
- A v1 line nested past Python's recursion limit crashed `parallax verify` with a RecursionError (about 1,100 deep on Python 3.12.3). The verifier now follows the nesting limit Polarizer 0.6.1 adds to LEDGER-SPEC.md: a line of either format nested more than 64 deep is `invalid` (structure), measured from its bytes before it's parsed, so every line gets a verdict. Polarizer 0.6.0's own verify had the same crash; 0.6.1 fixes it. Test: `test_a_line_may_nest_64_deep_and_no_deeper_in_either_version`.
- `conformance/` is copied again, byte for byte, from Polarizer at c8452a8: it adds `valid/nested_at_limit` and `broken/nested_too_deep`, new anchor cases in `expected.json`, and updated reference scripts. `tests/test_conformance.py` records the new commit and hashes; every chain fixture gets its expected verdict, and the anchors stay skipped by name.
- `parallax verify <folder> --head <file>` skipped a head file that didn't exist and printed intact. A head you name is now checked beside a folder as beside a file, and a missing one is an error. Test: `test_a_folder_checks_the_head_you_name_and_a_missing_one_is_an_error`.
- Field's tools could stay stuck. Parallax took each tool's state from its latest ledger entry, so one `tool.unservable` (which Polarizer writes once per process, and doesn't undo when a stored copy passes again) stopped every later run, and every stop said to approve with `--group`, which covers only tools with no decision and one definition waiting. The state now follows Polarizer's pin rules: new and changed last until a decision, and unservable counts only from the run's own serve. The card gives the exact commands: a group for the tools a group covers, and by name, with the hash, for the rest. A tool that can't be hashed, or is over 256 KiB, is named as one that can never be approved, with turning `polarizer` off as the way to run Field. Tests: `test_a_stored_copy_that_passes_again_stops_no_later_run`, `test_a_tool_serve_couldnt_serve_in_this_run_stops_with_the_step_that_applies`, `test_a_tool_with_a_decision_is_approved_by_name_and_the_card_says_how`.
- A `${` in the browser server's environment broke Polarizer's config: Polarizer expands a value that is exactly `${NAME}` and refuses any other value holding `${`, with no escape. Parallax now refuses such a value before writing the config, naming it, and the task stops for you. Test: `test_an_env_value_polarizer_would_expand_or_refuse_stops_before_the_config_is_written`; the test of the pinned Polarizer checks its parser refuses one too.
- With `XDG_DATA_HOME` outside `$HOME`, the sandbox could read Polarizer's ledger for Field. Every sandbox now denies that folder by name, as it does the approval key's, and no plan's `outside_reads` can include it. Test: `test_the_sandbox_never_reads_polarizers_field_ledger_wherever_xdg_puts_it`.

## 0.6.0 (2026-11-07)

- Polarizer adoption, Parallax's half. `parallax verify <path>` checks any hash-chained ledger in either format: Parallax's own (v0) or Polarizer's portable chain (v1, Polarizer's docs/LEDGER-SPEC.md), as a file, a file with `--head`, or a Polarizer ledger folder. One line out, exit 1 for anything but intact, and it only reads. `parallax/chainverify.py` is written from the spec, not from Polarizer's code; `parallax verify` with no path and the way Parallax writes its ledger are unchanged.
- `conformance/`, Polarizer's fixtures and expected verdicts, copied byte for byte from Polarizer at 700498a (`.gitattributes` keeps every checkout's bytes). `tests/test_conformance.py` runs the verifier over all 63 chain fixtures and matches every status, check, line and seq in `expected.json`, fails if any file differs from the hashes it lists, and skips the `anchors` section (Polarizer's signed checkpoints) by name.
- Field through Polarizer, off by default: `polarizer = true` under `[ui_tester]` runs Field's sandboxed browser server behind `polarizer serve`, which pins the tools' definitions and records every call in its own ledger at `~/.local/share/polarizer-field` (`parallax/fieldproxy.py`). Polarizer is called as a program, from PATH; nothing new is imported. A first run with nothing approved stops before Field spends anything, with the approve commands; after each run Parallax verifies Polarizer's ledger, and a tool waiting for approval, a held or refused call, a ledger that doesn't verify or a run Polarizer never recorded stops the task for you. Nothing is retried and `--no-holds` is never passed. Each run adds a `uitest.polarizer` ledger entry naming Polarizer's chain, session and seqs. Without the setting, Field and the policy's settings are exactly as before. The one real task through it hasn't been run; its steps are in `docs/using.md`.
- `scripts/test.sh` installs Polarizer 0.6.0 from its release wheel, pinned there, for the one test that runs the real program: its config parser takes the config Parallax writes, and `serve` refuses a ledger inside Parallax's folder.
- New tests: `tests/test_conformance.py`, `tests/test_chainverify.py` and `tests/test_fieldproxy.py`, with `tests/polarizer_fake.py` writing what serve would write.

## 0.5.0 (2026-11-07)

- M16's stats: `parallax stats` adds, from the ledger only, each task's rework cycles, how long it waited on you and how long it worked without you, and under the table, over finished tasks: the first-pass rate (Ready on its own with no rework cycle and no send-back), the median and most rework cycles with how many tasks hit the rework cap, and the median and longest wait with the median working time. A split request's row adds its parts' first pass, rework cycles, and its wait counted once across parts. Each measure, and what it doesn't count, is in `docs/using.md`. The rework-cap disagreement now records `rework_cap`; older entries are read from their reason. `status.derive` is now a fold of `status.step`, which stats uses to follow a task's state entry by entry (`parallax/measures.py`). Eleven tests in `tests/test_stats.py`, each on a constructed ledger with a hand-moved clock.

Fixed:
- Trusting the policy's commands once now answers every task waiting on them. Each task that needed `[build] setup` or `[merge] test_command` before you had trusted them stopped with its own card, so a split request asked once per part (tasks f291a5 and 64a75a, 2026-11-07, stopped on the same question). Your trust on one card now resolves every other open card about the same commands (the same hash) in the repo: each is recorded as resolved by your decision, citing its id, its task goes on, and `parallax stats` counts one touch. A card about different or changed commands still asks. Test: `test_one_trust_answers_every_card_that_asked_about_the_same_commands`.
- The check's test run no longer puts files the repo doesn't have in its copy. For a protected path the reviewed tree lacks (`mission.md` in Parallax's own repo), the sandbox stopped it being made by binding an unreadable placeholder at its name, and `tests/test_docs.py`, which reads every `.md` file in a copy with no git, failed with PermissionError, so every task whose plan ran it failed its check. The copy now denies writes to the protected paths it has and makes none up; Maker's sandbox still denies every protected path, present or not. Nothing outside the sandbox reads the copy back, and it's deleted after the run. Tests: `test_the_check_copy_denies_the_protected_paths_it_has_and_makes_none_up`, and in the real sandbox `test_in_the_real_sandbox_the_tests_see_the_reviewed_files_and_maker_still_cant_make_mission_md`.
- docs/macos.md named `build.is_alive`; the function is `build._alive`.

## 0.4.0 (2026-11-07)

- M15, the light conductor: several tasks from one request (`parallax/conductor.py`, `parallax/split.py`). With `[conductor]` in the policy, Focus first drafts `split.md`, the request's parts, each a goal and a scope, in the same normalize, lint and redraft loop as the intent and plan; one part is always allowed. Code checks it: every part has a goal and a scope, no scope names a protected path, at most `max_parts` (4); a split that still fails after two redrafts comes to you as one decision (one task, redraft, or drop). Each part is a task of its own through the same pilot, linked in the ledger to its request (`request.split`, and the request's id on each part's `task.created`). Up to `max_parallel` (2) parts work at once; parts whose scopes overlap run in turn, the later one made on the earlier one's accepted commit, and if the earlier one is dropped the later one asks you (build it on the base branch, or drop it). The parts launch under the launch rule when their caps together fit `auto_launch_usd`, otherwise after one question for all of them. `parallax inbox`, `parallax task list` and the UI's list group a request's parts under it, and `parallax stats` adds a line per split request. A policy without the table keeps every request one task, and evals stay one task. Ten tests, a UI server test, and two new stops in the every-stop walk. The live run is in `docs/conductor-runs.md`: the split and the launch together worked with real agents; both parts then stopped at this repo's trust question for its policy commands, which is the owner's, so neither reached Ready.

## 0.3.1 (2026-11-06)

Fixed:
- The knowledge layer gave later Makers text nobody approved. A dropped task contributed its drafted title and outcomes, or the goal as typed, which may be pasted issue text (invariant 10); it now gives its id, its date and your reason, nothing else. An accepted task's title and asked outcomes came from the current draft when there was one, so a redraft after the accept replaced the intent you approved; they now come from `docs/tasks/<id>/intent.md` in the accepted commit, nothing else.
- A past task's title went in at any length (one of 1,036 characters did). Every field is now one line of at most 200 characters.
- A task that ended twice kept its first ending's place, so the block wasn't newest first and the ten-task cap could drop the newest. A task now sits where its latest ending is.
- One past task whose intent couldn't be read (bytes that aren't UTF-8, a git failure) raised, before the Maker's own error handling, and stopped every later build in the repo. That task is now left out.
- Each call derived every task's status twice per finished task (20 passes for 10 tasks) and ran `git show` for each. It now derives once, skips git for a task with no intent, and keeps each accepted commit's intent for the life of the project object: 10 finished tasks cost one derive and at most 10 git calls, then none.
- CI: the workflow's comment said master's runs always finish. They didn't: a merge pushed while a run was going replaced the pending run of the merge before it (run 42151952768, at 6a0ef22, was cancelled before it started). Each push to master now has a concurrency group of its own, keyed by its commit, so none is cancelled or replaced.

Changed:
- The `claude` extra is bounded to the tested series, `claude-agent-sdk>=0.2.179,<0.3`; it had no bound and there is no lockfile. CI installs the extra, imports the adapter and checks that every SDK name it uses exists (`scripts/sdk_names.py`), with no model call.

Docs:
- `docs/THREAT_MODEL.md`'s memory row (ASI06) said no state carries across tasks besides the ledger; it now says what the knowledge block holds, who wrote each part, that Second Eye and Reticle never see it, and the risk left.
- `docs/security-review.md` called the test-writing verifier planned; it is Reticle, on by default since 2026-09-30.
- The 0.3.0 entry below said all six tools' lockups share one typeface. Loupe's and Max Q's don't; the entry is corrected in place, here and in the v0.3.0 release notes.
- The README's status line says all 11 eval cases reached Ready and 10 passed the maintainers' tests, so one Ready was wrong, with the eval's date and models; `docs/evals.md` names the models and says the cases may be in their training data. The headline run is dated and said to predate Reticle. The knowledge layer moved from Limits to What it does, leaving its limit behind. Terms are defined where they first appear, run and case ids are out of the prose, the CI badge is centered, and the README says who it's for. `docs/history.md` no longer calls the 2026-09-28 run the latest.

## 0.3.0 (2026-11-05)

- The knowledge layer: Focus and Maker are told what this repo's past tasks settled, compiled by code from the ledger (`parallax/knowledge.py`): the last ten finished tasks, newest first, each with its title, its asked outcomes, what you sent back and why, risks you accepted, and what you dropped. It is given as data beside the task's own material, which outranks it. Second Eye and Reticle never see it, and a repo with no finished tasks gets nothing. Five tests.
- The README's lockup and logo.svg set the word PARALLAX in a system sans at weight 800 with wide tracking, stretched to a fixed length, as Polarizer, ISR and Mordecai set theirs. (Corrected after release: this said all six tools' lockups share that type. Loupe's word is Unbounded SemiBold, as outlines, and Max Q's capitals are drawn as paths.) The pixel P is unchanged and stays crisp; the UI's header keeps its lowercase monospace word.
- Eval results record the scratch repo relative to the evals home, not as an absolute path, so a result file names no machine's home directory. The 41 recorded results were rewritten the same way; `evals.scratch_path` resolves old and new.

Fixed:
- A cloned repo's own `parallax.policy.toml` ran its `[build] setup` and `[merge] test_command` as you on the first build or merge, with no prompt. The first task to need either now stops with a card showing both commands; trust records your answer once for the repo, and a changed command asks again.
- A task the UI couldn't display was noted only in the terminal running `parallax ui`. The failure now goes to the ledger too (`view.broken`: the task, where it broke, the error's one line), once per distinct error, and the broken row, card and `parallax task list` cite that entry.

Changed:
- `parallax/accept.py` calls git through `parallax/git.py` like the rest, instead of nineteen direct `subprocess.run` calls. No behavior change.

Docs:
- The README is restructured and shorter (133 lines to 94): the plain sentence and the status line first, then What it does, Try it, What was measured, Limits, How it was built and The other tools. Setup detail, Stop and remove, the tests and the full list of limits moved to `docs/using.md`. `tests/test_readme_rules.py` holds the README to under 155 lines, no dashes as punctuation, links that resolve, no bare placeholders and no "only", "first" or "best" in its prose. `docs/brand/README.md` says what each brand file is and where it is used.

## 0.2.3 (2026-10-11)

Changed:
- The UI reads the ledger once per change, not several times per task on every refresh: parsed entries are kept while the file's size and modified time stay the same, and dropped on every write. Entries a read returns are shared, so they're read-only.
- One git helper, `parallax/git.py`, replaces six. Error messages fall back to git's stdout when stderr is empty, and every module reads git's output as UTF-8.

Docs:
- The README shows the demo first, says what it takes to start (Linux or WSL2, root apt installs, the AppArmor sysctl, Node 22, a Claude Code login), and installs from the release tag with uv: the `parallax` on PyPI is an unrelated SSH tool. The day-to-day steps and keys moved to `docs/using.md`.
- `docs/macos.md`: what running on macOS with srt would take, and a plan. Not supported yet.
- CLAUDE.md points at `docs/parallax.md`; `docs/history.md` cites eval run 8e3074. `evals/README.md` lists the three runs no doc linked. `docs/PRODUCT.md` and seven unused screenshots (1.1 MB) are gone.

## 0.2.2 (2026-10-11)

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
