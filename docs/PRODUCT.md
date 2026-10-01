# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: a developer who owns a repository and hands work to agents. They type work in, agents do it without them, and they make one judgment per task. They come to the UI between other work, often after being away, to see what needs them and decide.

## Product Purpose

Parallax is hands-free. Execution happens without the user. They only make judgment calls, and when they make them, they have everything they need to make them well. Any feature that adds work for them instead of removing it is wrong.

The UI (`parallax ui`) exists so the user can see at a glance what needs them and decide well, fast. Success is one touch per normal task: accept, or reject with a reason. `parallax stats` measures it.

## Positioning

Agents do the work; the human makes the calls. The maker builds in a sandbox, a blind checker reviews only the outcome, constraints and diff, and code (never a model) turns everything waiting on the user into exactly one item per task: Ready, or one Decision needed with its options and a code-written recommendation. Parallax never merges without your click: Accept shows the merge command, and Accept and merge lands the commit on the base branch locally on that click, fast-forwarding it when it is still the tip and merging it into the task first when it has moved on, never forcing and never pushing.

## Operating Context

- Runs locally in WSL2 (or macOS/Linux). The server is stdlib Python on 127.0.0.1; the user usually opens it from a Windows browser at `localhost`.
- A token in the URL fragment unlocks the page; a strict CSP forbids inline script and style.
- The same data is in the terminal: `parallax inbox`, `parallax show <task>`, `parallax diff <task>`. The card in the UI is exactly `parallax show`.
- Tasks move through drafting, building, checking, then Ready or needs you, then done. Several can run at once. Reject redrafts the task from the user's reason, and a send-back changes only what the reason names: code checks the redraft against the version sent back. Drop ends it.
- Accept and merge shows the task as Merging from the click until it lands or stops: the step it's on, how long it has run, and how long it usually takes (the median of the last five pre-merge test runs; Parallax's own suite takes about 4 minutes). A conflict leaves the base branch where it was, and the card lists the git commands to finish the merge by hand: check out the task's branch, merge the base branch in, fix and add the files, `git commit --no-edit`, then `git merge --ff-only` the task's branch from the base branch. A merged card is built from the ledger and the repo's commits, so it no longer needs the task's worktree.
- Everything the user reads follows one output shape (docs/parallax.md): Type, Bottom line, Not looked at, Next, then Decisions, Changed since last time, Found, Recommended, Details. Header under 40 words, body under 150.

## Capabilities and Constraints

- Intake box, board of tasks by state, one card per task, accept, reject with a reason (redraft or drop), answer a Decision needed, view the diff, intent and plan, live updates, keyboard use.
- A budget named over the policy's limit for the task's size asks once: allow it for this task, or use the limit. When it's also over auto_launch_usd, "Allow $8 and launch" (with the amount) answers the budget and the launch together, for the plan on the card; a changed plan asks again.
- When a new task's scope names a file an older open task also changes, the newer task's card, `parallax inbox` and `parallax show` give a heads-up naming the other task and the files. It never blocks.
- A protected doc (CLAUDE.md, REVIEW.md, docs/parallax.md and the like) is one Maker can't edit. When a change makes one wrong, Focus's plan lists it under "Docs you'll need to update", the Ready card says to update it yourself before merging and why, and Second Eye treats a stale one as a note, never a block.
- When the tests, Reticle's tests or Field's flows fail, their whole output is kept as a file in the task's data folder, with its path and hash in the ledger. The card's failure line links to it (shown only while the hash matches), and the Ask box can read it.
- No merge without your click: the only merge is Accept and merge, local, never pushed; a fast-forward, or the base branch merged in and the test gate re-run when it has moved on. Rejects, overrides and accepted risks need a reason; approvals don't.
- Agent-written text is shown as text, never HTML. No inline code. No external requests.
- Plain static HTML, CSS and JS served from `parallax/web/`, no build step, no framework, no runtime dependencies (inferred from the repo; stdlib only in `parallax/`).
- No em dashes anywhere in UI copy.

## Brand Commitments

- Name: Parallax. Voice: plain and direct; one-line confirmations are lowercase and short.
- Clean room: no names, code or UI copied from other orchestrators.

## Evidence on Hand

Real task records live in the ledger (`.parallax/ledger.jsonl`) and `docs/tasks/`. There are no testimonials, customers or benchmarks, and none may be invented.

## Product Principles

1. Less is more. No bloat: every element earns its place or goes.
2. The next action is always obvious.
3. A decision card reads in 30 seconds and holds everything needed to decide well.
4. Nothing competes for attention: what needs the user outranks everything else.
5. No dashboards, no charts, no decoration, no generic AI-app look.

## Accessibility & Inclusion

Usable by keyboard alone. Works in a narrow window. (No other requirement stated.)
