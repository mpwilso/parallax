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
- **Evals (2026-09-30):** the harness returns on the current pipeline: `parallax eval` runs cases through `do`'s pipeline under a hard budget, and `parallax stats` says when the evals are older than a prompt, a model, CLAUDE.md, REVIEW.md or the policy.
- **The knowledge layer (2026-11-05):** Focus and Maker get what the repo's past tasks settled, compiled by code from the ledger: the last ten finished tasks, their asked outcomes, what you sent back and why, risks you accepted, and what you dropped. Second Eye and Reticle stay blind to it.
- **M15 (2026-11-06):** the light conductor. Focus may split a request into parts, checked by code; each part is a task of its own, up to two at a time, parts that touch the same files in turn on the earlier one's accepted commit, and one question only when their caps together pass `auto_launch_usd`. Its live run split and launched two parts together, then stopped at a question that was the owner's: [conductor-runs.md](conductor-runs.md).
- **Next:** M16, evals and stats: first-pass rate, rework cycles and wait time. Eval results so far: [evals.md](evals.md).

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
Its last run (2026-09-28, 11 cases from 6 projects, $12.12 in all): **10 of 11 fixes resolved**; the
blind checker was right 8 times, missed 1 bad fix and raised 1 false alarm. Reports:
[67faab](../evals/results/2026-09-28-67faab.md), [73982e](../evals/results/2026-09-28-73982e.md).
These committed results came from an earlier harness that has since been removed, with the
old path it ran on. The cases in [evals/cases.toml](../evals/cases.toml) are the case set for the
harness on the current pipeline ("Evals" in [plan.md](plan.md)). Its run 8e3074 (2026-10-01)
passed 10 of 11: [evals.md](evals.md#results).
