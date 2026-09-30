# Evals

## What they measure

Whether Parallax, running hands-free exactly as `parallax do` does, fixes real bugs the way the projects' own maintainers did, and whether Second Eye's verdict matches that truth.

Each case is a bug that was fixed and merged in a small open-source Python project. Parallax gets the repository as it was before the fix, and the issue text as a person would type it. The tests the maintainers added with their fix stay hidden from every agent. After the run, Parallax runs those tests itself, in the sandbox. The cases are in [evals/cases.toml](../evals/cases.toml), and the design is in [docs/plan.md](plan.md#evals-2026-09-30-the-harness-on-the-current-pipeline).

## How a case is scored

- **Passed:** the fix reached Ready and passes the maintainers' hidden tests. A fix nothing reviewed never counts, even if its tests pass.
- **Second Eye against the truth:** on each change it judged, a pass on a good fix is right, a pass on a bad fix is a miss, a fail on a bad fix is a catch, and a fail on a good fix is a false alarm.
- **Cost** (estimated dollars at list prices), **time to Ready**, and **touches**: each decision the task raised, plus the final accept or reject.

## Current results

11 cases from 6 projects, each case's latest run. Raw results are in [evals/results/](../evals/results/).

| Case | Hidden tests | Second Eye | Cost | Time to Ready | Touches | Run |
|---|---|---|---|---|---|---|
| tomlkit-430 | pass | right | $0.54 | 1.5 min | 1 | 419a5e |
| tabulate-190 | pass | right | $0.65 | 2.2 min | 1 | 419a5e |
| boltons-319 | pass | right | $0.51 | 1.9 min | 1 | 419a5e |
| tabulate-180 | pass | right | $0.38 | 1.4 min | 1 | 41250f |
| boltons-337 | pass | right | $0.54 | 2.0 min | 1 | 41250f |
| boltons-348 | pass | right | $0.55 | 1.5 min | 1 | 41250f |
| humanize-174 | **fail** (356 of 379) | **miss** | $0.56 | 1.8 min | 1 | 41250f |
| pathspec-77 | pass | right | $0.94 | 2.2 min | 1 | 7f9c74 |
| tomlkit-512 | pass | right | $0.95 | 3.5 min | 1 | 7f9c74 |
| cachetools-387 | pass | right | $1.15 | 3.3 min | 1 | 7f9c74 |
| tabulate-231 | pass | right | $1.45 | 4.8 min | 1 | 7f9c74 |

- **Passed:** 10 of 11. All 11 reached Ready.
- **Second Eye:** right 10 times and missed 1 bad fix. It caught none and raised no false alarms.
- **Cost:** $8.21 for these 11 runs. All three eval runs cost $11.14 together, counting four earlier attempts that stopped at their cap.
- **Touches:** 1 per case.

The last four cases first ran in 41250f and stopped at caps of $0.67 to $0.80, which Focus had set too low for the work. That led to a floor on every plan's cap. Rerun with it in 7f9c74, all four reached Ready.

## What they show

- On small bugs in small pure-Python projects, the hands-free pipeline mostly gets there. 10 of 11 fixes matched the maintainers' tests, each with one touch.
- **Second Eye can pass a fix that looks right but isn't.** humanize-174's issue says `naturaldelta` always rounds down. Maker made hours round to the nearest hour, and its own tests passed. The maintainers made it round to the nearest unit for minutes, hours and months, and 23 of their tests catch the difference. Second Eye sees only the outcome, the review rules and the diff. The diff looked right for hours, and the other units weren't in it. Its own report said it hadn't seen the rest of the file or the other tests, and hadn't run anything. The same case was missed by the previous harness too.

## Reticle, and catch rates from seeded versions (2026-09-30)

Reticle writes tests of what you asked, before the build; Maker can't see or change them. Eleven real fixes hold only one bad one, which gives no catch rate. So `parallax eval --seeded` needs no Maker: code breaks each maintainers' fix (flips one comparison, shifts one integer, or undoes one hunk), and keeps up to three versions per case that fail the hidden tests. Each version, and the real fix, gets Second Eye's exact normal input and a run of Reticle's kept tests. Anything flagged on the real fix is a false alarm.

**Seeded versions are mechanical edits, not real mistakes.** The rates below describe that kind of error: one wrong operator, constant or missing hunk inside a fix that is otherwise the maintainers' own. They don't say how often either checker catches the mistakes Maker actually makes.

23 broken versions from 10 of the 11 cases (cachetools-387 gave none that failed the hidden tests), and 11 real fixes. Runs 9ba608-seeded and 48a3a5-seeded for Reticle, 221c3e-seeded for Second Eye.

| | Broken versions caught | False alarms on the real fixes |
|---|---|---|
| Reticle, tests of what you asked | 19 of 23 (it had a test for 22) | 1 of 10 it had a test for (tabulate-231) |
| Second Eye, findings about behavior | 23 of 23 | 7 of 11 |

- **Reticle** missed three versions its tests didn't reach, and had no test at all for tabulate-190. It costs about $0.03 to $0.08 a task. It's on by default since these results (`[reticle] enabled = true`).
- **Second Eye** is scored in this mode on findings about behavior. No seeded version has tests, since they're the hidden ones, so a finding that only says a test is missing would flag every version and every real fix; those are kept with each result but don't count. By hand, one of the 7 false alarms (tomlkit-512) is only such a finding in words the rule missed, so 6 of 11. One more (tomlkit-430) counts only because a CHANGELOG entry is missing. Most of the rest are the real fix doing less than Focus's intent asks, such as tabulate-180 guarding one of two blocks the intent names, or doing something it doesn't ask, such as boltons-348 changing the CI workflow. Second Eye judges the change against the intent, not against what the maintainers decided.
- **Inferred-outcome notes were tried and rejected.** With Reticle also testing the outcomes Focus inferred, failures shown as notes on the card and never sent back, the seeded runs showed 5 notes on the 11 real fixes, all noise by definition, for 3 more catches (22 of 23). On humanize-174 run through the whole pipeline (7eb145), the one case where a note could have mattered, there was none. Reticle now tests only what you asked.

**Why humanize-174 gets past every checker:** the rule the fix broke wasn't in the request. The issue asks that `naturaldelta(10799)` say "3 hours", not "2 hours". The maintainers went further and made every unit round to the nearest one, minutes and months too, and their hidden tests check that. Reticle tests what you asked, and Maker gets hours right, so its tests pass. Focus inferred the other units as an outcome, but Reticle doesn't test inferred outcomes, and the inferred-outcome test it wrote in 7eb145 was for hours, which passed. Second Eye sees only the diff, which looks right for hours. A checker can only hold a fix to a rule that someone wrote down.

## What they don't show

- **How often Second Eye catches a bad fix Maker made.** Only one fix was bad, and it wasn't caught. One case can't give a rate. The seeded rates above are for mechanical edits.
- **Its false-alarm rate on Maker's fixes.** None in 11 judgments is too few to say it's rare. On the maintainers' real fixes, seeded, it was 7 of 11.
- **Anything beyond small, pure-Python repos,** or any variance: each case ran once.
- **An independent test of the cap floor.** Its $2.00 default came from Maker's costs on these same cases, so the rerun succeeding is expected.
- **One consistent version.** The runs span three commits of Parallax.

`parallax eval --budget <usd> [case ...]` reruns them, and `parallax stats` says when the results are older than a prompt, a model or the policy.
