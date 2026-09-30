Type: FYI
Bottom line: Of 23 broken versions, Second Eye caught 20 and Reticle 19.
Not looked at: nothing
Next: you read Details, then decide on Reticle.
Found
- Second Eye caught 20 of 23 broken versions (evals/results/2026-09-30-f5ff69-seeded/run.json:1)
- Reticle's tests of asked outcomes caught 19 of 23; 22 had a test (evals/results/2026-09-30-f5ff69-seeded/run.json:1)
- with its inferred-outcome tests too, Reticle caught 22 of 23 (evals/results/2026-09-30-f5ff69-seeded/run.json:1)
- on the 11 real fixes: Second Eye 4 false alarms, Reticle 1, and 5 inferred-outcome notes (evals/results/2026-09-30-f5ff69-seeded/run.json:1)
Details
- Run f5ff69 on parallax 9cf882a, budget $2.00, $0.75 spent.
- Second Eye alone, scored on findings about behavior. Each case's versions, intent and Reticle results come from the seeded run named on its line.
- pathspec-77: undo the hunk at pathspec/patterns/gitwildmatch.py:316: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right, 1 notes. $0.05. From run 9ba608.
- tomlkit-430: flip == to != at tomlkit/api.py:266: Second Eye catch, Reticle catch; change 1 to 2 at tomlkit/api.py:266: Second Eye catch, Reticle catch; change 0 to 1 at tomlkit/api.py:267: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right, 0 notes. $0.06. From run 9ba608.
- tomlkit-512: undo the hunk at tomlkit/items.py:2120: Second Eye catch, Reticle catch; undo the hunk at tomlkit/items.py:2138: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right, 0 notes. $0.07. From run 9ba608.
- cachetools-387: no broken version failed the hidden tests. Real fix: Second Eye right, Reticle right, 0 notes. $0.02. From run 9ba608.
- tabulate-180: change 0 to 1 at tabulate/__init__.py:1507: Second Eye catch, Reticle miss; undo the hunk at tabulate/__init__.py:1506: Second Eye miss, Reticle catch; undo the hunk at tabulate/__init__.py:2069: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right, 1 notes. $0.09. From run 9ba608.
- tabulate-190: flip != to == at tabulate/__init__.py:1521: Second Eye catch, Reticle no test. Real fix: Second Eye false alarm, Reticle no test, 0 notes. $0.03. From run 9ba608.
- tabulate-231: flip == to != at tabulate/__init__.py:105: Second Eye catch, Reticle catch; change 1 to 2 at tabulate/__init__.py:111: Second Eye catch, Reticle catch; undo the hunk at tabulate/__init__.py:104: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle false alarm, 1 notes. $0.07. From run 9ba608.
- boltons-319: change 12 to 13 at boltons/timeutils.py:381: Second Eye catch, Reticle catch; change 1 to 2 at boltons/timeutils.py:394: Second Eye catch, Reticle catch; undo the hunk at boltons/timeutils.py:393: Second Eye miss, Reticle catch. Real fix: Second Eye right, Reticle right, 0 notes. $0.08. From run 48a3a5.
- boltons-337: undo the hunk at boltons/dictutils.py:74: Second Eye catch, Reticle catch; undo the hunk at boltons/dictutils.py:178: Second Eye catch, Reticle catch; undo the hunk at boltons/dictutils.py:194: Second Eye miss, Reticle miss. Real fix: Second Eye right, Reticle right, 1 notes. $0.13. From run 48a3a5.
- boltons-348: undo the hunk at boltons/cacheutils.py:240: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 0 notes. $0.05. From run 48a3a5.
- humanize-174: flip == to != at src/humanize/time.py:162: Second Eye catch, Reticle catch; change 0 to 1 at src/humanize/time.py:162: Second Eye catch, Reticle catch; flip < to <= at src/humanize/time.py:162: Second Eye catch, Reticle miss. Real fix: Second Eye false alarm, Reticle right, 1 notes. $0.11. From run 48a3a5.

