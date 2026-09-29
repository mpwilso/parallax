# Real UI runs (Part 2, step 7)

Driven through `parallax ui` by Playwright, the way a person uses it: type the work in, watch it
move, decide from the card. Real agents (drafters, maker, checker, UI tester) in a scratch copy of
this repo, 2026-09-28 and 29. Screenshots of each state are in each run's folder, in order;
`tester-saw-*.png` are the UI tester's own screenshots, as they appear on the card. `run.json` is
the driver's timeline.

Time to Ready counts agent time from intake: time a task waited on a person is left out.
Costs are estimates at API list prices, from the ledger.

## The three runs

| Run | Result | Touches | Time to Ready | Drafters | Maker | Checker | UI tester | Total |
|---|---|---|---|---|---|---|---|---|
| 1. `remove the em dash on README.md line 81` | Ready, accepted | 1: accept | 3m 22s | $0.41 | $0.48 | $0.03 | not run | $0.92 |
| 2. UI: a count beside each queue heading | Ready, accepted | 2: launch confirm, accept | 7m 38s | $1.11 | $1.50 | $0.03 | $0.08 | $2.72 |
| 3. UI: show each task's id; rejected at Ready, redrafted | Ready twice, accepted | 2: reject, accept | 5m 34s, then 10m 55s after the reject | $1.51 | $2.59 | $0.05 | $0.29 (two runs) | $4.44 |

- **Run 1** (`1-no-touches/`): intake to Ready with no touch. The checker's gap (it couldn't see all of README.md) sits under Not looked at.
- **Run 2** (`2-ui-change/`): the launch waited for a confirm because the cap ($3.90, with the UI tester's share) was over the $3 `auto_launch_usd` then; it's $5 now. The UI tester wrote two flow tests, Parallax ran them on the build it had used (both passed), and again at the check (2 of 2).
- **Run 3** (`3-reject-redraft/`): Ready in 5m 34s with 3 UI flows passing. Rejected with "Only on rows that wait on you. Working and done rows stay as they are." The drafters redrafted the intent from it ("showing the task id on the rows that wait on you"), and it came back Ready with 4 of 4 new UI flows passing, 25 of 25 plan tests, and the checker finding nothing blocking.

## Run 2 and a rework round

The run 2 that passed went straight to Ready. An earlier attempt, 2da12f, did three real rework
rounds before it came to a person, all recorded in the ledger:

1. The checker found the headings rendered with an em dash when the outcome asked for the comma form (blocker, `parallax/web/app.js:104`). The UI tester's four flows failed on that build too (0 of 4).
2. The maker fixed the format; flows went to 3 of 4. The one left was a UI tester test counting `<article>` rows the page never had.
3. After a cap raise, the maker reworked again and wrapped every row in an `<article>` to satisfy that test: 4 of 4 flows, and the checker caught it as going against the intent.

The rework path is also covered without a model: `test_an_added_em_dash_is_reworked_before_the_checker_sees_it`,
`test_a_failing_flow_is_reworked_and_the_tester_runs_once`, and the M10 rework tests.

## Every real run, and what each failed attempt found

Total spent on real runs: **$27.71** ($8.08 on the three that passed, $19.63 on the attempts that found bugs).
Each bug was fixed, with a test, before the next attempt.

| Task | Cost | Ended | What it found | Fix |
|---|---|---|---|---|
| 032849 | $0.92 | run 1, accepted | nothing | |
| a56043 | $1.72 | out of cap in the build | drafting outran the plan's estimate, so the rework floor collapsed | b1a2419: code raises a short cap to drafting plus twice the estimate plus the tester's share |
| 482a6e | $2.19 | tests couldn't run | the check's sandbox had no browser for this repo's own browser tests (the machine also slept 13 hours mid-run) | 1869c67: the check's tests get the tester's pinned browser |
| d7f384 | $2.62 | flows couldn't run | the demo app couldn't start in the sandbox (git refused without a readable .gitconfig), and the tester wrote tests without ever reaching it; the flow config sat where the sandbox couldn't read it | 66a19c6: a run without the app-up mark fails closed; git gets no global config |
| 964571 | $3.01 | maker couldn't fix | the tester's own tests were broken and pinned on the maker; the em dash check flagged the tester's file | 28c6eb8: its tests run once on the build it used, and a failing one for a flow it said works is dropped |
| 2da12f | $5.19 | intent vs plan | three rework rounds (above); a tester test that stayed wrong cost the cap; the pilot ran the repo's copy of Parallax | dda54fc: a test still failing after a rework comes to you; 3b2ddfa: the pilot runs with `python -P` |
| e703c4 | $2.49 | maker blocked | the checker judged the tester's tests as part of the change | ffb7c21: they live in docs/tasks/<task>/ui_flows, outside the checker's diff (invariant 3) |
| 3bbc03 | $2.72 | run 2, accepted | nothing | |
| a42589 | $2.41 | tests dropped, to you | the demo app made new task ids every start, so tests naming them failed on the next start | 2663ae6: the demo app's ids are fixed |
| 29f523 | $4.44 | run 3, accepted | nothing (see the gap below) | |

A gap run 3 shows, not fixed yet: after a redraft, the card doesn't say the task was redrafted or from what reason.

## The UI tester's share, and what it caught

It cost $0.08 of run 2 (2.9%) and $0.29 of run 3 (6.5%, two tester runs). Across all nine UI task
attempts it was $0.89 of $26.79 (3.3%). Drafting was the largest share of every UI task.

It hasn't caught anything the checker missed. In 2da12f it flagged the em dash headings on the same
build the checker did. In the runs that passed, every flow it walked worked, and the checker agreed.
