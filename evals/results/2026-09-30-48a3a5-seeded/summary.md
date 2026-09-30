Type: FYI
Bottom line: Of 10 broken versions, Second Eye caught 10 and Reticle 8.
Not looked at: nothing
Next: you read Details, then decide on Reticle.
Found
- Second Eye caught 10 of 10 broken versions (evals/results/2026-09-30-48a3a5-seeded/run.json:1)
- Reticle's tests of asked outcomes caught 8 of 10; 10 had a test (evals/results/2026-09-30-48a3a5-seeded/run.json:1)
- with its inferred-outcome tests too, Reticle caught 10 of 10 (evals/results/2026-09-30-48a3a5-seeded/run.json:1)
- on the 4 real fixes: Second Eye 4 false alarms, Reticle 0, and 2 inferred-outcome notes (evals/results/2026-09-30-48a3a5-seeded/run.json:1)
Details
- Run 48a3a5 on parallax 1aa96dc, budget $1.90, $0.67 spent.
- boltons-319: change 12 to 13 at boltons/timeutils.py:381: Second Eye catch, Reticle catch; change 1 to 2 at boltons/timeutils.py:394: Second Eye catch, Reticle catch; undo the hunk at boltons/timeutils.py:393: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 0 notes. $0.18.
- boltons-337: undo the hunk at boltons/dictutils.py:74: Second Eye catch, Reticle catch; undo the hunk at boltons/dictutils.py:178: Second Eye catch, Reticle catch; undo the hunk at boltons/dictutils.py:194: Second Eye catch, Reticle miss. Real fix: Second Eye false alarm, Reticle right, 1 notes. $0.18.
- boltons-348: undo the hunk at boltons/cacheutils.py:240: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 0 notes. $0.12.
- humanize-174: flip == to != at src/humanize/time.py:162: Second Eye catch, Reticle catch; change 0 to 1 at src/humanize/time.py:162: Second Eye catch, Reticle catch; flip < to <= at src/humanize/time.py:162: Second Eye catch, Reticle miss. Real fix: Second Eye false alarm, Reticle right, 1 notes. $0.19.

