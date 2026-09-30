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

## What they don't show

- **How often Second Eye catches a bad fix.** Only one fix was bad, and it wasn't caught. One case can't give a rate.
- **Its false-alarm rate.** None in 11 judgments is too few to say it's rare.
- **Anything beyond small, pure-Python repos,** or any variance: each case ran once.
- **An independent test of the cap floor.** Its $2.00 default came from Maker's costs on these same cases, so the rerun succeeding is expected.
- **One consistent version.** The runs span three commits of Parallax.

`parallax eval --budget <usd> [case ...]` reruns them, and `parallax stats` says when the results are older than a prompt, a model or the policy.
