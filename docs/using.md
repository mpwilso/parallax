# Using Parallax

The short version is in the README's [Day to day](../README.md#day-to-day). This is the rest.

## The task box

If what you paste reads like a list of build steps (numbered steps naming branches, commits and batches) rather than one task, the box says so first: "This looks like a list of build steps. Send it as one task anyway?" It's a heads-up: you can send it anyway.

## The list

Waiting on you: the only part that needs you, riskiest first. Working: which agent has each task. Each row shows its stages, what it has spent against its cap, and for how long. Done: folded away, with each task's outcome, cost and touches. With no card open, the page says how it's going.

## A card

It shows the stages, the bottom line, the one question with its options and which one is recommended, what nobody looked at, and the evidence, with the change, the intent and the plan one click away. Ask about the task in the box on the card: a model answers from that task's record only, and can't change anything.

## Accept, or accept and merge

Accept commits the reviewed change to the task's branch and shows the merge command, which you run. Accept and merge also lands it on your base branch here: a fast-forward when your base branch is still where the task began, or your base branch merged into the task first when it has moved on. Either way your project's tests run on the exact commit your base branch would become (`[merge] test_command` in the policy; Parallax's own is `scripts/test.sh`), and that gate is the only check. If they fail, or the merge hits a conflict, your base branch doesn't move, and the card says why and leaves the merge to you. Parallax never merges without your click, and never pushes.

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
- **The full output of a failed check** is a click away from the failure line.
