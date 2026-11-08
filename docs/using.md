# Using Parallax

The one-line version is step 5 of the README's [Try it](../README.md#try-it). This is the rest, with setup, removal, the tests and the known limits at the end.

## Day to day

1. **Start it.** In your repo, run `parallax ui` and open the link it prints. The link stays the same between runs.
2. **Type the task** into the box at the top and press Enter. That's all.
3. **Watch the list.** Waiting on you: the only part that needs you, riskiest first. Working: which agent has each task, and what it has spent against its cap.
4. **Open a card.** It shows the bottom line, the one question with its options and which one is recommended, what nobody looked at, and the evidence. Ask about the task in the box on the card: a model answers from that task's record only, and can't change anything.
5. **Accept, or accept and merge.** Accept commits the reviewed change to the task's branch and shows the merge command, which you run. Accept and merge also lands it on your base branch, once your project's tests pass on the exact commit your base branch would become; Parallax never merges without your click, and never pushes.

## Keys and the terminal

Keys in the app: `/` type a task, `n` next waiting, `j` and `k` move, `a` then `Enter` accept, `r` reject, `1` to `9` then `Enter` pick an option, `d` the change, `Esc` back.

The terminal has the same: `parallax do "..."`, `parallax inbox`, `parallax show <task>`, `parallax accept <task>`, `parallax reject <task> --reason "..."`, `parallax decide <task> <option>`, `parallax stats`.

## The page

It works by keyboard alone, with a visible focus outline on everything you can tab to, and in a narrow window. It follows your system's light or dark theme; the picker in the header overrides it, in that browser only. Body and status text meet WCAG AA contrast (4.5:1) in both themes, every stage symbol has a name (like "Maker: failed"), and with reduced motion on, nothing moves.

## The task box

If what you paste reads like a list of build steps (numbered steps naming branches, commits and batches) rather than one task, the box says so first: "This looks like a list of build steps. Send it as one task anyway?" It's a heads-up: you can send it anyway.

## The list

Waiting on you: the only part that needs you, riskiest first. Working: which agent has each task. Each row shows its stages, what it has spent against its cap, and for how long. Done: folded away, with each task's outcome, cost and touches. With no card open, the page says how it's going.

## A card

It shows the stages, the bottom line, the one question with its options and which one is recommended, what nobody looked at, and the evidence, with the change, the intent and the plan one click away. Ask about the task in the box on the card: a model answers from that task's record only, and can't change anything.

**Ready** means the listed checks passed: the plan's tests ran on the exact reviewed tree, the card says for each outcome in the intent which test that ran covers it or that none does, and Second Eye found nothing blocking. It does not mean the code is bug-free. **Needs you** means one decision that has to be yours; the card says whose call it is and why a person has to make it. Anything that sends work back, drops it or accepts a risk asks for a one-line reason, and Focus redrafts from it.

## Accept, or accept and merge

Accept commits the reviewed change to the task's branch and shows the merge command, which you run. Accept and merge also lands it on your base branch here: a fast-forward when your base branch is still where the task began, or your base branch merged into the task first when it has moved on. Either way your project's tests run on the exact commit your base branch would become (`[merge] test_command` in the policy; Parallax's own is `scripts/test.sh`), and that gate is the only check. That command runs as you, so the first time, and again whenever it or `[build] setup` changes, the card shows both commands and asks before running them; trust records your answer for the repo. One trust answers every open card that asks about the same commands, so the parts of a split request stopped on that question all go on, and a card about different commands still asks. If they fail, or the merge hits a conflict, your base branch doesn't move, and the card says why and leaves the merge to you. Parallax never merges without your click, and never pushes.

## Merging

From your click on Accept and merge until it lands or stops, the task shows as Merging. The card says which step it's on (merging your base branch in, or running the tests), how long it has run, and how long it usually takes here: the median of the last five pre-merge test runs. Parallax's own suite takes about 4 minutes. The buttons stay greyed out until it's done.

## A conflict

If merging your base branch in hits a conflict, nothing moves, and the card gives you the commands to finish it in your repo's folder: `git checkout` the task's branch, `git merge` your base branch, fix the files it names and `git add` them, `git commit --no-edit`, then `git checkout` your base branch and `git merge --ff-only` the task's branch.

## Stop, at any stage

Every running card has Stop: while Focus drafts, Reticle writes tests, Maker builds, the checks and Field run, and during the pre-merge test run. It asks once, then that task's agents and processes end within a few seconds, recorded as your action with what it had spent. A stopped task waits on you: resume it from where it stopped (finished stages aren't done again), send it back with a reason, or drop it. Stopping during the pre-merge test run never moves your base branch.

## Several tasks from one request

Type two unrelated fixes in one go, and Focus may split them: before the intent, it writes `split.md`, the parts of the request, each a goal and the files it may touch. Most requests stay one part, and then nothing changes. Code checks the split (every part has a goal and files, none touches a protected path, at most `[conductor] max_parts`, 4 by default); one that doesn't hold goes back to Focus twice, then comes to you as one card: do it as one task, redraft the split from your reason, or drop it.

Each part is a task of its own, with its own card, cap and checks. In the list, a request's parts sit together under it, each with its own state, and `parallax inbox` and `parallax task list` group them the same way. Up to `[conductor] max_parallel` parts (2 by default) work at once. Parts that touch the same files run in turn: the later one waits, shown as "waiting on" the earlier task, until you accept that one, and is built on its accepted commit. If you drop the earlier one instead, the later one comes to you: build it on the base branch as it is, or drop it.

Each Ready part is accepted on its own. The parts launch without you when their caps together are at most `auto_launch_usd`; when they're over, one card asks once for all of them, and your launch starts them all. `parallax stats` adds a line per split request: its parts, their touches and cost. A policy without the `[conductor]` table keeps every request one task, as before.

## Stats

`parallax stats` reads the ledger only. One row per task: your touches, its cost, its rework cycles, how long it waited on you and how long it worked without you; then a row per split request. The lines under it count finished tasks only (accepted, merged, rejected or dropped), because an open task's numbers aren't final; on an open task's row the two times show as `-`.

- **Touches:** every approve, reject, decision, accept, cap raise or stop you made, every command you ran to fix a task up, and every drafted file you edited by hand. The target is one: the final accept or reject. Describing the work and running the merge don't count.
- **First pass:** the finished tasks that reached Ready on their own, with no rework cycle and no send-back, out of all finished tasks. Ready on its own means the checks passed; a task that was Ready only because you overruled Second Eye doesn't count, and nor does one that came back Ready after you sent it back. It doesn't count the decisions before Ready (a launch over your limit, a budget, a trust question), and it doesn't say whether you accepted the result: a task you dropped at Ready still passed first time.
- **Rework cycles:** how many times Maker reworked a task after a failed check, across every attempt, with the median and the most for one task, and how many tasks came to you because they hit the rework cap. It doesn't count redrafts by Focus, the one retry of a task that stopped with no work, or your send-backs, which first pass counts.
- **Waiting on you:** how long a task sat on the board as Ready or Needs you (a decision needed, Ready, or stopped by you), from the moment it got there to your next answer, accept, reject, stop or command for that task, or until it moved on by itself. One trust answer that also clears other cards ends their waits too. The line gives the median per finished task and the longest, with its task. It doesn't count the time before the task existed, or a merge you run yourself after an accept. A part of a split request held until another part's launch question is answered counts as working; the request's row counts that wait once, on the part that asked.
- **Working without you:** the rest of the task's time, from `parallax do` to the entry that finished it, less its waits. It includes drafting, building, checking, waiting for a free place among a request's parts, and an Accept and merge's test run. It isn't the agents' run time: an agent's own minutes are in its session records.
- **A split request's row:** first pass as its finished parts out of its finished parts, the rework cycles of all its parts, and, once every part is finished, the time any of its parts waited on you, counted once where parts waited together, and the rest of the request's time from its first part's start to its last part's finish.

## After the merge

Parallax removes the task's worktree and deletes its branch, once your next command sees the accepted commit in your branch. The card stays readable: it's built from the ledger and the commits in your repo. Dropping a task keeps its work as a patch in the task's data folder (`~/.local/share/parallax/tasks/`), then removes its worktree and branch.

## What agents know of past tasks

Focus drafts, and Maker builds, with a block code writes from the ledger: this repo's last ten finished tasks, newest first. An accepted task gives its title and asked outcomes, read from the intent in its accepted commit (the file you approved, never a later draft), what you sent back and why, and risks you accepted. A dropped task gives its id, its date and your reason, never its goal or its drafts, which nobody approved. A past task whose intent can't be read is left out. It is given as data beside the task's own material, and the task's intent and plan win over it. Second Eye and Reticle never see it: both judge the work against the intent alone. A repo with no finished tasks gets no block.

## Where drafts live

A task's drafts (intent, plan, Reticle's tests, Field's flows) live in that data folder too, never in your checkout. Accept still writes `docs/tasks/<task>/` (intent, plan and record) into the task's commit.

## Spending, chosen once

The first time Parallax runs in a repo (`parallax init` in a terminal, or else the first time you open `parallax ui`), it asks one question: how should Parallax handle spending? Your answer goes in `parallax.policy.toml` as `[budget] mode`:

- **Ask me before a task goes over a limit** (`ask`, the default): each task has a cap, at most $5 for a small task and $20 for a large one, and it stops and asks before going over.
- **Keep going, and stop only at $25 a task** (`ceiling`; set the amount with `ceiling_usd`).
- **No limit** (`none`): it never stops for money.

A budget you name in the request always wins, and above the mode's limit it asks you once. In every mode, no limit included, a task stops and asks when the same check fails the same way twice in a row, and says what keeps failing. The overview shows the current mode; change it with `parallax budget ask`, `ceiling` or `none`, or the button under the overview. The dollars are Claude Code's estimates at API list prices: on a Claude subscription they measure how much a task used, not a charge.

## Cards that tell you first

A few cards tell you something before you have to ask:

- **A budget over the limit.** If your request names a budget above the policy's limit for its size (`small_cap_usd` or `large_cap_usd`), Focus drafts once and the card asks one question: allow that budget for this task only, or use the limit. Nothing is redrafted to fit. When the budget is also over `auto_launch_usd`, the first choice is "Allow $8 and launch" (with your amount): one answer covers the budget and the launch of the plan you can read on the card. If that plan changes before it launches, the card asks again.
- **Overlapping tasks.** When a new task's scope names a file that an older open task also changes (one waiting on you, Ready, or accepted but not merged), the newer task's card, `parallax inbox` and `parallax show` name the other task and the files. It's a heads-up: it never blocks anything.
- **A send-back changes only what you name.** When you reject at Ready or send a task back, Focus gets the version it drafted with your reason and is told to change only what the reason asks. Code then compares the intent's outcomes, constraints and scope with that version; anything removed or rewritten that your reason doesn't mention goes back to Focus to restore.
- **Docs you'll need to update.** Maker can't edit a protected doc such as CLAUDE.md or REVIEW.md. When a change makes one wrong, the Ready card names it and says to update it yourself before merging.
- **A sandbox that didn't start.** Preflight runs before every build. If the sandbox can't start, the build doesn't launch, and the card says so, quotes the line `bwrap` or `srt` printed, and gives one next step: allow user namespaces (the README's `sysctl` line, and [wsl.md](wsl.md#allow-user-namespaces) on WSL) or install the tool it names, then retry. It names `parallax doctor`, which checks the sandbox once that's done. The sandbox's full output is a click away.
- **The full output of a failed check** is a click away from the failure line.

## Checking a ledger

`parallax verify` checks this repo's ledger, as it always has. `parallax verify <path>` checks any hash-chained ledger in either of two formats, from any folder: a file in Parallax's own format (v0) or in Polarizer's (v1), or a Polarizer ledger folder, whose `ledger.head` is checked with it (`--head <file>` names one beside a file). It prints one line, such as `intact (v1): 13 entries, chain <id>, head <hash> at seq 12` or the first problem with its line and seq, `tampered: line 6 (seq 15): seq is 15, expected 5`, and exits 1 for anything but intact. It only reads.

The rules are Part 1 of Polarizer's [LEDGER-SPEC.md](https://github.com/mpwilso/polarizer/blob/master/docs/LEDGER-SPEC.md), written into `parallax/chainverify.py` from the spec. `conformance/` is Polarizer's set of fixtures and expected verdicts, copied unchanged from Polarizer at 700498a; `tests/test_conformance.py` checks every verdict it records and fails if a file in the folder differs from the hashes it lists. Two things it doesn't do: Polarizer's signed checkpoints (`polarizer verify --anchor`, the `anchors` section of `expected.json`), which the tests skip by name, and Polarizer's argument side files (`verify --args`). One difference from `parallax verify` with no path: a blank line is invalid here, as the spec says, where Parallax's own check skips it. Parallax never writes one.

## Field through Polarizer

Off by default. With `polarizer = true` under `[ui_tester]` in `parallax.policy.toml`, Field's browser server runs behind `polarizer serve`, from [Polarizer](https://github.com/mpwilso/polarizer) 0.6.0, installed on its own (`uv tool install https://github.com/mpwilso/polarizer/releases/download/v0.6.0/polarizer-0.6.0-py3-none-any.whl`). Polarizer then pins the definitions of the browser tools Field gets and records every call Field makes, in its own ledger. Agents can't turn it on or off: the policy file is a protected path. Without the line, Field runs exactly as before.

- **Where it runs.** Polarizer runs as you, outside the sandbox, started by Field's session in place of the browser server. The app, the browser and the server stay in the same sandbox as before, now as Polarizer's one upstream; nothing in the sandbox can reach Polarizer or its files. Field's browser tools are all given Polarizer's `open-world` class (each answer is page text from the app), so Polarizer's rules hold none of them, and the tools Field may not use are refused before they reach Polarizer.
- **Its ledger** is `~/.local/share/polarizer-field` (under `XDG_DATA_HOME` when that's set), one for every Field run on the machine, so an approval lasts. It is never inside `~/.local/share/parallax` or `~/.config/parallax`, which Polarizer refuses, and a policy file can't move it: a cloned repo can turn the route on, but can't point Polarizer's writes anywhere. `parallax verify ~/.local/share/polarizer-field` checks it.
- **Approving the tools, once.** Polarizer hides each tool until you approve its definition. When nothing of Field's is approved yet, Parallax first runs `polarizer serve` with its input closed, which lists the tools and exits, and the task stops before Field spends anything. The card gives the commands: `polarizer pending --ledger-dir ~/.local/share/polarizer-field` prints each definition, `polarizer approve --ledger-dir ~/.local/share/polarizer-field --group <id>` approves the ones it groups, and any its scanner flags are approved by name after you read them. Then choose Retry on the card. The server also lists two tools Field never gets, `browser_file_upload` and `browser_run_code`: whether you approve them changes nothing, since Parallax refuses them before they reach Polarizer. A definition that changes later is hidden again, and the task stops the same way.
- **Every other stop comes to you, and nothing is retried.** After each run Parallax verifies Polarizer's ledger and reads that run's session. A tool waiting for approval, a held call, a refused call, a ledger that doesn't verify, or a run Polarizer never recorded stops the task with a card that names it, and Field's tests from that run aren't kept. A held call waits in Polarizer until you run `polarizer allow` or `deny`, or until Polarizer's 300 s timeout refuses it; then the task stops for you. Field never runs with `--no-holds`.
- **The record.** Each run adds a `uitest.polarizer` entry to the task's ledger: Polarizer's ledger folder, its chain id, the run's session and the seqs it spans, Polarizer's version, and how many calls, refusals and holds there were, so what Field touched can be found and checked later.

**The one real run hasn't been done.** The route is tested with a stand-in for what Polarizer writes, and with the real Polarizer 0.6.0 for the config and the refused folder; no task has gone through it with a real model and a real browser. The steps, for when it is run:

1. Install Polarizer 0.6.0 with the line above, and check `command -v polarizer` finds it on the PATH that `parallax ui` runs with.
2. In a repo with a web app and `[ui_tester]` on, add `polarizer = true` under `[ui_tester]`.
3. `parallax do` a small task whose plan names a user flow. The first check stops at the approval card: run the commands it gives, then Retry.
4. At Ready, or wherever it stops: `parallax verify ~/.local/share/polarizer-field` says intact, and `parallax log` shows the `uitest.polarizer` entry whose seqs hold Field's calls. Record the run in [history.md](history.md).

## Setup

The steps are in the README's [Try it](../README.md#try-it). What they do:

- **Linux, or Windows through WSL2.** On Windows, make the distro first ([wsl.md](wsl.md)), then run the steps inside it. It needs Claude Code's sandbox (bubblewrap), which doesn't run on native Windows. macOS is untested: what it would take is in [macos.md](macos.md).
- **Step 1, as root:** git, bubblewrap, socat and ripgrep for the sandbox, Node 22 from NodeSource (apt's Node is too old for the sandbox runtime), and `@anthropic-ai/sandbox-runtime`. Ubuntu 24.04 and later block the unprivileged user namespaces bubblewrap needs, so `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0` allows them (CI does the same); the same line in `/etc/sysctl.d/99-parallax.conf` keeps it across reboots.
- **Step 2, as your user:** uv, then Claude Code, logged in. Parallax needs Claude Code and the Claude Agent SDK.
- **Step 3:** Parallax from GitHub, since the `parallax` on PyPI is an unrelated SSH tool. To work on Parallax or run its evals, clone it and install that instead: `git clone https://github.com/mpwilso/parallax ~/code/parallax`, then `uv tool install --editable "$HOME/code/parallax[claude]"`.
- **Step 4, `parallax doctor`:** `build tools` covers every program a build runs: git, uv, and whatever your `[build] setup` calls. Parallax also looks in `~/.local/bin`, where the uv installer puts it, so a shell without uv on its PATH still builds. Anything still missing is printed by `parallax ui` when it starts and shown at the top of the page, with the fix, before any task runs. Run in your repo, `repo` checks it has a commit and a branch checked out: every task starts from your latest commit, and Accept and merge lands on the branch you're on. In a Parallax project, `test runner` checks that the python your tests run with has pytest when there's no `[build] setup`; with one, make sure setup installs it.
- **Step 5, `parallax init`:** if the repo's tests need dependencies, set `[build] setup` in `parallax.policy.toml` to the command that makes a venv at `$PARALLAX_VENV`; `init` shows the command and asks first when the repo's example policy already has one.

## Stop and remove

`Ctrl+C` in the terminal running `parallax ui` stops the page; `parallax stop` ends every running task now and records it; `parallax stop <task>` ends just that one. To remove Parallax: `uv tool uninstall parallax`, then delete `~/.local/share/parallax` (worktrees, task folders, Field's tools) and `~/.config/parallax` (the approval key and UI links). A repo keeps nothing but `parallax.policy.toml`, `REVIEW.md`, its ledger in `.parallax/` and the `docs/tasks/` it accepted; delete those to leave no trace.

## Running the tests

The tests never call a model. `scripts/test.sh` runs ruff and the whole suite exactly as CI does, with the browser tests; `scripts/test.sh browser` fetches the pinned Chromium once. Chromium also needs three system libraries, libnss3, libnspr4 and libasound2t64: `sudo apt-get install -y libnss3 libnspr4 libasound2t64`. Without them the browser tests skip, and say why. The three real-sandbox tests need a machine that allows unprivileged user namespaces; elsewhere they skip and say why. `scripts/test.sh` also installs Polarizer 0.6.0 from its release wheel, pinned there, for the one test that runs the real `polarizer`; every other Polarizer test needs no install. `scripts/demo.py` re-records the README's demo.

## Limits

- **Second Eye reads the diff and can't run code.** It judges the outcome where the diff shows it, and says what it couldn't see. Reticle's tests do run: a test that passes on the code before the change is dropped, so every kept test was seen failing before the fix. Against 23 mechanically broken versions of real fixes, Reticle or Second Eye caught 20, with 2 false alarms on the 11 real fixes ([evals.md](evals.md#the-checkers-together)). Those are seeded edits, not the mistakes Maker actually makes.
- **A requirements gap gets past every checker.** When Focus guesses wrong about what was wanted, every check passes the guess, because each one judges the work against that intent (humanize-174 in [evals.md](evals.md#results)).
- **Linux or WSL2.** The sandbox doesn't run on native Windows, and macOS is untested ([macos.md](macos.md)).
- **It needs Claude Code**, logged in, and the Claude Agent SDK. Costs are Claude Code's estimates at list prices; on a subscription your real limit is the plan's usage limits, which Parallax can't see.
- **A cap can overshoot by one turn.** Every agent gets what's left of the cap as its own limit, but the SDK checks it between turns.
- **Field's browser server is pinned** to `@playwright/mcp` 0.0.70: later versions need Unix sockets the sandbox refuses.
- **Field through Polarizer has a 20 s start.** Polarizer waits at most 20 s for its upstream, so an app that takes longer to answer leaves Field with no browser tools, and the task stops for you. Not run with a real task yet. One approval should last: in a trial on 2026-11-07, Polarizer at its commit 700498a listed the pinned server twice, with a different output folder and origin each time, as two tasks would, and recorded no new definition the second time.
- **One person, one machine.** Parallax runs here, for you: there is no shared server or team inbox.
- **A split is judged by its scopes.** Code runs parts side by side only when their scopes can't touch the same file, and it errs toward running them in turn. It can't tell whether two parts that touch different files still depend on each other; Focus is told to split only work that stands apart.
- **What agents know of the past is what the ledger holds.** Focus and Maker get a block compiled by code from this repo's last ten finished tasks: the titles and asked outcomes of the intents you approved, and your own reasons. Nothing a person wrote outside a task reaches them, and a task's own intent and plan outrank the block.

What Parallax protects and what it can't: [THREAT_MODEL.md](THREAT_MODEL.md).
