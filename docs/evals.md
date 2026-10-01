# Evals

## The checkers together

Two agents check every change before it reaches you: Reticle runs its tests of what you asked, and Second Eye reviews the diff blind. A broken change counts as caught if either one catches it, and a correct change counts as a false alarm if either one blocks it.

Eleven real fixes hold only one bad one, which gives no catch rate. So `parallax eval --seeded` needs no Maker: code breaks each maintainers' fix (flips one comparison, shifts one integer, or undoes one hunk), and keeps up to three versions per case that fail the hidden tests. Each version, and the maintainers' real fix, gets Second Eye's exact normal input and a run of Reticle's kept tests. Anything blocked on the real fix is a false alarm.

**Seeded versions are mechanical edits, not real mistakes.** The rates describe that kind of error: one wrong operator, constant or missing hunk inside a fix that is otherwise the maintainers' own. They don't say how often the checkers catch the mistakes Maker actually makes.

23 broken versions from 10 of the 11 cases (cachetools-387 gave none that failed the hidden tests), and 11 real fixes. The latest run is [9a3432-seeded](../evals/results/2026-10-01-9a3432-seeded/summary.md) (2026-10-01), a full seeded run: Focus drafted each intent again, Reticle wrote its tests again, and Second Eye reviewed against those intents. The earlier figures are kept for comparison: Reticle from [9ba608-seeded](../evals/results/2026-09-30-9ba608-seeded/summary.md) and [48a3a5-seeded](../evals/results/2026-09-30-48a3a5-seeded/summary.md), with Second Eye reviewing those runs' intents in [cbcd0e-seeded](../evals/results/2026-09-30-cbcd0e-seeded/summary.md) and again in [e1f0a0-seeded](../evals/results/2026-10-01-e1f0a0-seeded/summary.md).

| | Broken versions caught | Real fixes blocked (false alarms) |
|---|---|---|
| **Reticle or Second Eye, now (9a3432)** | **21 of 23** | **3 of 11** |
| Reticle or Second Eye, before (9ba608 and 48a3a5, Second Eye in cbcd0e) | 22 of 23 | 2 of 11 |

By hand both are 21 of 23: see boltons-337 and humanize-174 below.

- **Missed by both, now:** tabulate-180's two versions with a hunk undone, each removing one of the fix's guards for an empty table with `maxcolwidths`. Second Eye passed both, as in every run, and this time Reticle kept no test that reaches them. boltons-337's version with a hunk undone at dictutils.py:194, missed by both before, is caught by Second Eye now.
- **False alarms, now:** all three are Second Eye's.
  - humanize-174, on the same constraint as before: Focus wrote that results under an hour stay the same, and the maintainers' broader fix changes them.
  - tabulate-190: Focus inferred an outcome (4: blank lines inside a cell are kept) that the maintainers' fix doesn't meet. Second Eye's blocking finding rests on it but doesn't cite it, so the code that lowers a finding resting only on inferred outcomes to a note never saw it. The citation is the checker's own, and nothing checks it against the finding's text.
  - boltons-337: a pickle of an empty dict made before the fix doesn't load the same way after it. The outcome didn't ask about old pickles, and the maintainers' fix passes every hidden test.
  - tabulate-231's false alarm before was Reticle's. Its test this time passes the real fix.

## Each checker on its own

| | Broken versions caught | False alarms on the real fixes |
|---|---|---|
| Reticle now (9a3432), tests of what you asked | 18 of 23 (it had a test for 20) | 0 of 10 it had a test for |
| Reticle before (9ba608 and 48a3a5) | 19 of 23 (it had a test for 22) | 1 of 10 it had a test for (tabulate-231) |
| Second Eye in 221c3e: outcomes without their marks | 23 of 23 by the rule, 22 by hand | 7 of 11 by the rule, 6 by hand |
| Second Eye in f5ff69: marked outcomes, asked-only by instruction | 20 of 23 by the rule and by hand | 4 of 11 by the rule, 3 by hand |
| Second Eye in cbcd0e: asked-only enforced by code, findings by kind | 20 of 23 by the rule, 19 by hand | 1 of 11 (humanize-174) |
| Second Eye in e1f0a0: the same, and a stale protected doc is a note, on cbcd0e's intents | 19 of 23 by the rule and by hand | 1 of 11 (humanize-174), by the rule and by hand |
| Second Eye now (9a3432): the same rules, on intents Focus drafted again | 20 of 23 by the rule, 19 by hand | 3 of 11 (humanize-174, tabulate-190, boltons-337), by the rule and by hand |

- **Reticle** before missed three versions its tests didn't reach, and had no test at all for tabulate-190. In 9a3432 it kept no test that reaches tabulate-180's three versions, missed boltons-337's hunk at dictutils.py:194 and humanize-174's `<` flipped to `<=`, and blocked no real fix. It costs about $0.02 to $0.08 a task. It's on by default since these results (`[reticle] enabled = true`).
- **What changed for Second Eye, and why.** In 221c3e most of its false alarms were correct fixes doing less than Focus's intent asked, and much of that intent was Focus's own inferred outcomes, the same ones behind Reticle's early false alarms. Three changes followed, each rerun on the same versions and intents:
  - It sees each outcome marked asked or inferred, and its rules let a blocking or scope finding rest only on an asked outcome or a constraint. The review template makes housekeeping (a changelog entry, docs) a note unless a repo's REVIEW.md says otherwise. In f5ff69 false alarms fell from 6 to 3 of 11 by hand, but on tabulate-190 it still failed a correct fix on inferred outcomes 3 and 4, against its rule.
  - Code enforces the rule: each finding says what it cites, and a blocking finding that cites only inferred outcomes becomes a note, recorded in the ledger. tabulate-190's real fix passes in cbcd0e.
  - Each finding carries a kind (behavior, missing_test, scope or housekeeping), and the seeded mode counts only behavior and scope, by that field. Before, a phrase-matching rule guessed which findings only asked for a test, and missed some wordings, which is why earlier runs have separate counts by hand. No seeded version has tests, since they're the hidden ones, so a missing test would flag every version.
  - Catches stayed at 20 of 23. Two of the misses are tabulate-180's: each version undoes one of the fix's two guards for an empty table with `maxcolwidths`, which breaks an asked outcome, and Second Eye passed both; Reticle's tests caught both. (An earlier version of this page said those versions broke only an inferred outcome. They don't.) boltons-337's old catch in 221c3e had rested only on a missing test, so it was never a real one.
- **The rerun on 2026-10-01 (e1f0a0)**, after two changes since cbcd0e: Second Eye's fixed rules now say a change that makes a protected doc (CLAUDE.md, REVIEW.md and the like) wrong is a note, never a reason to fail; and a task now stops when the same check fails the same way twice in a row. The second doesn't touch this eval, which judges each version once and never reworks. The first can't explain the one change either, since no seeded diff touches a protected doc.
  - 19 of 23 caught, one fewer: pathspec-77's version with its hunk undone, which leaves only a comment's typo fixed. In cbcd0e Second Eye said the change doesn't make `[^...]` a negation; this time it raised nothing at all. Same diff, same intent. It's run-to-run variance, like boltons-319's in f5ff69 and cbcd0e. Reticle still catches it, so the two together are unchanged.
  - By hand, cbcd0e had one catch fewer than its rule counted: humanize-174's version with `<` flipped to `<=`. Its findings there were the maintainers' minute and month rounding, the same ones behind the false alarm on the real fix, so none was about the flipped comparison. In e1f0a0 Second Eye names the flip itself (one day and a few seconds now reads as hours), so the catch counts by hand.
  - False alarms are unchanged: humanize-174's real fix, on the same constraint as before. The other ten real fixes drew no blocking finding.
  - The run cost $0.80, against $0.79 for cbcd0e.
- **The full seeded rerun on 2026-10-01 (9a3432)** drafted every intent again, so the review is against different words than before. That, more than any change to Parallax, is what moved its numbers:
  - Second Eye caught 20 of 23 by the rule, the same count as cbcd0e. By hand it's 19: its catch of boltons-337's hunk undone at dictutils.py:74 rests only on the old-pickle finding above, which applies to the real fix too, and says nothing of the `PY3` flag that undoing the hunk leaves undefined. Reticle still catches that version.
  - The two new false alarms come from what the intents say: an inferred outcome the real fix doesn't meet (tabulate-190) and a question the outcome never raised (boltons-337). The tabulate-190 one shows a gap in how the asked-only rule is enforced: it trusts the finding's own citation.
  - The run cost $1.57 for all 11 cases.
- **Inferred-outcome notes were tried and rejected.** With Reticle also testing the outcomes Focus inferred, failures shown as notes on the card and never sent back, the seeded runs showed 5 notes on the 11 real fixes, all noise by definition, for 3 more catches (22 of 23). On humanize-174 run through the whole pipeline (7eb145), the one case where a note could have mattered, there was none. Reticle now tests only what you asked.

**Why humanize-174 gets past every checker:** the rule the fix broke wasn't in the request. The issue asks that `naturaldelta(10799)` say "3 hours", not "2 hours". The maintainers went further and made every unit round to the nearest one, minutes and months too, and their hidden tests check that. Reticle tests what you asked, and Maker gets hours right, so its tests pass. Focus inferred the other units as an outcome, but Reticle doesn't test inferred outcomes, and the inferred-outcome test it wrote in 7eb145 was for hours, which passed. Second Eye sees only the diff, which looks right for hours. A checker can only hold a fix to a rule that someone wrote down.

## The full pipeline on the 11 cases

### What it measures

Whether Parallax, running hands-free exactly as `parallax do` does, fixes real bugs the way the projects' own maintainers did, and whether Second Eye's verdict matches that truth.

Each case is a bug that was fixed and merged in a small open-source Python project. Parallax gets the repository as it was before the fix, and the issue text as a person would type it. The tests the maintainers added with their fix stay hidden from every agent. After the run, Parallax runs those tests itself, in the sandbox. The cases are in [evals/cases.toml](../evals/cases.toml), and the design is in [docs/plan.md](plan.md#evals-2026-09-30-the-harness-on-the-current-pipeline).

### How a case is scored

- **Passed:** the fix reached Ready and passes the maintainers' hidden tests. A fix nothing reviewed never counts, even if its tests pass.
- **Second Eye against the truth:** on each change it judged, a pass on a good fix is right, a pass on a bad fix is a miss, a fail on a bad fix is a catch, and a fail on a good fix is a false alarm.
- **Cost** (estimated dollars at list prices), **time to Ready**, and **touches**: each decision the task raised, plus the final accept or reject.

### Results

Run [8e3074](../evals/results/2026-10-01-8e3074/summary.md) (2026-10-01): all 11 cases in one run, on one commit (ba09b02), with Reticle on, as it is by default. Raw results are in [evals/results/](../evals/results/).

| Case | Hidden tests | Second Eye | Reticle | Cost | Time to Ready | Touches |
|---|---|---|---|---|---|---|
| pathspec-77 | pass | right | right | $0.85 | 2.3 min | 1 |
| tomlkit-430 | pass | right | right | $0.49 | 1.8 min | 1 |
| tomlkit-512 | pass | right | right | $1.33 | 4.4 min | 1 |
| cachetools-387 | pass | right | right | $1.26 | 3.1 min | 1 |
| tabulate-180 | pass | right | right | $0.34 | 1.4 min | 1 |
| tabulate-190 | pass | right | right | $0.56 | 1.9 min | 1 |
| tabulate-231 | pass | right | right | $1.31 | 4.3 min | 1 |
| boltons-319 | pass | right | right | $0.57 | 2.6 min | 1 |
| boltons-337 | pass | right | right | $0.85 | 3.0 min | 1 |
| boltons-348 | pass | right | right | $0.63 | 2.1 min | 1 |
| humanize-174 | **fail** (376 of 379) | **miss** | **miss** | $1.49 | 4.4 min | 1 |

- **Passed:** 10 of 11, as before. All 11 reached Ready.
- **Second Eye:** right 10 times and missed 1 bad fix, as before. It caught none and raised no false alarms. Reticle the same, for $0.28 of the run's cost.
- **Cost:** $9.68 for the one run, under a $20 budget. Before, $8.21 for the same 11 cases across three runs with Reticle off.
- **Touches:** 1 per case. No case needed a rework, so the loop protection (a task stops when the same check fails the same way twice) never acted.
- **humanize-174** fails 3 hidden tests instead of 23. This time Focus's intent inferred nearest-unit rounding for every unit, so Maker rounded minutes, hours and days. Two of the three failures are months that should carry into a year (364 days reads "11 months", not "a year"); the third is 2 hours 30 minutes, which the maintainers' Python `round()` takes to "2 hours" and Maker's round-half-up to "3 hours". Second Eye and Reticle both passed it: Reticle tests only what you asked, and the units beyond hours were Focus's inferred outcomes.

### Previous results (2026-09-30)

11 cases from 6 projects, each case's latest run then, Reticle off.

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

- On small bugs in small pure-Python projects, the hands-free pipeline mostly gets there. 10 of 11 fixes matched the maintainers' tests, each with one touch, in both full runs.
- **Second Eye can pass a fix that looks right but isn't.** humanize-174's issue says `naturaldelta` always rounds down. Maker made hours round to the nearest hour, and its own tests passed. The maintainers made it round to the nearest unit for minutes, hours and months, and 23 of their tests catch the difference. Second Eye sees only the outcome, the review rules and the diff. The diff looked right for hours, and the other units weren't in it. Its own report said it hadn't seen the rest of the file or the other tests, and hadn't run anything. The same case was missed by the previous harness too. In 8e3074 Focus inferred rounding for every unit and Maker rounded minutes, hours and days too; 3 of the maintainers' tests still catch it (months that should carry into a year, and a half hour rounded up where Python's `round()` goes to even), and Second Eye passed it again.

## What they don't show

- **How often Second Eye catches a bad fix Maker made.** Only one fix was bad, and it wasn't caught. One case can't give a rate. The seeded rates above are for mechanical edits.
- **Its false-alarm rate on Maker's fixes.** None in 11 judgments, in either full run, is too few to say it's rare. On the maintainers' real fixes, seeded, it went from 6 of 11 to 1 of 11 as its rule changed, stayed at 1 of 11 in e1f0a0, and was 3 of 11 in 9a3432 on intents drafted again (above).
- **Variance.** Each seeded run is one run. Second Eye's catches on the same versions moved between runs (boltons-319's undone hunk was missed in f5ff69 and caught in cbcd0e; pathspec-77's was caught in cbcd0e and missed in e1f0a0), so a difference of one or two isn't a trend.
- **Anything beyond small, pure-Python repos,** or any variance: each case ran once.
- **An independent test of the cap floor.** Its $2.00 default came from Maker's costs on these same cases, so the rerun succeeding is expected.
- **One consistent version, before.** The previous results span three commits of Parallax. 8e3074 and 9a3432 are each one run on one commit (ba09b02).

`parallax eval --budget <usd> [case ...]` reruns them, and `parallax stats` says when the results are older than a prompt, a model or the policy.
