# History

How Parallax got here, milestone by milestone. The design notes behind each step are in
[plan.md](plan.md) and [direction.md](direction.md); the real runs of the current UI are in
[ui-runs/](ui-runs/README.md).

## Milestones

- **M1:** the policy file, the ledger, a task in its own worktree.
- **M2:** a maker and a blind checker, verdicts, and disagreements routed to a human.
- **M3:** a conductor on a pulse, driven by a mission file, several tasks in parallel.
- **M4:** evidence moved the line both ways: promotion and law proposals, each approved by a human.
- **M5:** evals against real merged open-source fixes, with results published.
- **M6:** a local visual inbox.
- **M7:** the move to WSL2; the conductor, pulse, profiles, promotions and laws were cut; `parallax doctor`.
- **M8:** intent, spec and plan as files, and the output shape with `parallax lint`.
- **M9:** the sandboxed build, preflight and `parallax stop`.
- **M10:** the check: code checks, the plan's tests in the sandbox, the blind checker, rework.
- **M11:** accept, the implementation record, the threat model.
- **M12:** hands-free: `parallax do` drafts, checks the plan against the intent, launches under the policy's rule, builds and checks, ending in one inbox item. `parallax stats` counts human touches.
- **M13:** the one decision: every stop is one question with options and a recommendation written by code; reject at Ready redrafts from your reason.
- **M14:** the UI as the main surface; then a UX pass (a queue, cards built for the decision) and the UI tester, a blind agent that uses the app in a real browser.
- **Next:** M15, the light conductor (splitting work into tasks, scheduling), and M16, evals on the current pipeline and more stats.

## The first design (Milestones 1 to 6)

Parallax started as a permission layer: a maker worked a task straight from its goal, every
action it took was ruled on by a policy file (allow, ask or deny), "ask" became a decision in your
inbox, and a checker reviewed the diff blind. That path proved the ledger, the worktrees and the
blind checker, and taught the main lesson: asking the human about actions turns review into a
rubber stamp. From M8 on, the human approves outcomes instead (an intent and a plan), and the
agents are bounded by the sandbox and the plan. The old path was cut on 2026-09-29.

## Evals

The first design was measured against already-merged open-source fixes: Parallax got the issue
text only, and was scored by the tests the humans added in their pull request, which it never saw.
Latest run (2026-09-28, 11 cases from 6 projects, $12.12 in all): **10 of 11 fixes resolved**; the
blind checker was right 8 times, missed 1 bad fix and raised 1 false alarm. Reports:
[67faab](../evals/results/2026-09-28-67faab.md), [73982e](../evals/results/2026-09-28-73982e.md).
The evals ran on the old path and were cut with it; the cases are kept in
[evals/cases.toml](../evals/cases.toml), and the evals come back on the current pipeline in M16.
