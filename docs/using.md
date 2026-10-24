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

Accept commits the reviewed change to the task's branch and shows the merge command, which you run. Accept and merge also lands it on your base branch here: a fast-forward when your base branch is still where the task began, or your base branch merged into the task first when it has moved on. Either way your project's tests run on the exact commit your base branch would become (`[merge] test_command` in the policy; Parallax's own is `scripts/test.sh`), and that gate is the only check. That command runs as you, so the first time, and again whenever it or `[build] setup` changes, the card shows both commands and asks before running them; trust records your answer for the repo. If they fail, or the merge hits a conflict, your base branch doesn't move, and the card says why and leaves the merge to you. Parallax never merges without your click, and never pushes.

## Merging

From your click on Accept and merge until it lands or stops, the task shows as Merging. The card says which step it's on (merging your base branch in, or running the tests), how long it has run, and how long it usually takes here: the median of the last five pre-merge test runs. Parallax's own suite takes about 4 minutes. The buttons stay greyed out until it's done.

## A conflict

If merging your base branch in hits a conflict, nothing moves, and the card gives you the commands to finish it in your repo's folder: `git checkout` the task's branch, `git merge` your base branch, fix the files it names and `git add` them, `git commit --no-edit`, then `git checkout` your base branch and `git merge --ff-only` the task's branch.

## Stop, at any stage

Every running card has Stop: while Focus drafts, Reticle writes tests, Maker builds, the checks and Field run, and during the pre-merge test run. It asks once, then that task's agents and processes end within a few seconds, recorded as your action with what it had spent. A stopped task waits on you: resume it from where it stopped (finished stages aren't done again), send it back with a reason, or drop it. Stopping during the pre-merge test run never moves your base branch.

## After the merge

Parallax removes the task's worktree and deletes its branch, once your next command sees the accepted commit in your branch. The card stays readable: it's built from the ledger and the commits in your repo. Dropping a task keeps its work as a patch in the task's data folder (`~/.local/share/parallax/tasks/`), then removes its worktree and branch.

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

The tests never call a model. `scripts/test.sh` runs ruff and the whole suite exactly as CI does, with the browser tests; `scripts/test.sh browser` fetches the pinned Chromium once. Chromium also needs three system libraries, libnss3, libnspr4 and libasound2t64: `sudo apt-get install -y libnss3 libnspr4 libasound2t64`. Without them the browser tests skip, and say why. The three real-sandbox tests need a machine that allows unprivileged user namespaces; elsewhere they skip and say why. `scripts/demo.py` re-records the README's demo.

## Limits

- **Second Eye reads the diff and can't run code.** It judges the outcome where the diff shows it, and says what it couldn't see. Reticle's tests do run: a test that passes on the code before the change is dropped, so every kept test was seen failing before the fix. Against 23 mechanically broken versions of real fixes, Reticle or Second Eye caught 20, with 2 false alarms on the 11 real fixes ([evals.md](evals.md#the-checkers-together)). Those are seeded edits, not the mistakes Maker actually makes.
- **A requirements gap gets past every checker.** When Focus guesses wrong about what was wanted, every check passes the guess, because each one judges the work against that intent (humanize-174 in [evals.md](evals.md#results)).
- **Linux or WSL2.** The sandbox doesn't run on native Windows, and macOS is untested ([macos.md](macos.md)).
- **It needs Claude Code**, logged in, and the Claude Agent SDK. Costs are Claude Code's estimates at list prices; on a subscription your real limit is the plan's usage limits, which Parallax can't see.
- **A cap can overshoot by one turn.** Every agent gets what's left of the cap as its own limit, but the SDK checks it between turns.
- **Field's browser server is pinned** to `@playwright/mcp` 0.0.70: later versions need Unix sockets the sandbox refuses.
- **One person, one machine.** No splitting work into several tasks yet; several tasks from one request, and a knowledge layer that gives agents project context and past decisions to draw on, are planned.

What Parallax protects and what it can't: [THREAT_MODEL.md](THREAT_MODEL.md).
