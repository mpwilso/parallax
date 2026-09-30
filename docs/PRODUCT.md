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

Agents do the work; the human makes the calls. The maker builds in a sandbox, a blind checker reviews only the outcome, constraints and diff, and code (never a model) turns everything waiting on the user into exactly one item per task: Ready, or one Decision needed with its options and a code-written recommendation. Parallax never merges without your click: Accept shows the merge command, and Accept and merge fast-forwards the base branch locally on that click, never forcing and never pushing.

## Operating Context

- Runs locally in WSL2 (or macOS/Linux). The server is stdlib Python on 127.0.0.1; the user usually opens it from a Windows browser at `localhost`.
- A token in the URL fragment unlocks the page; a strict CSP forbids inline script and style.
- The same data is in the terminal: `parallax inbox`, `parallax show <task>`, `parallax diff <task>`. The card in the UI is exactly `parallax show`.
- Tasks move through drafting, building, checking, then Ready or needs you, then done. Several can run at once. Reject redrafts the task from the user's reason; drop ends it.
- Everything the user reads follows one output shape (docs/parallax.md): Type, Bottom line, Not looked at, Next, then Decisions, Changed since last time, Found, Recommended, Details. Header under 40 words, body under 150.

## Capabilities and Constraints

- Intake box, board of tasks by state, one card per task, accept, reject with a reason (redraft or drop), answer a Decision needed, view the diff, intent and plan, live updates, keyboard use.
- No merge without your click: the only merge is Accept and merge, fast-forward only, local, never pushed. Rejects, overrides and accepted risks need a reason; approvals don't.
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
