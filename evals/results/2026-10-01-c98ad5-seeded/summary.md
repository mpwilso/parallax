Type: FYI
Bottom line: Of 23 broken versions, Second Eye caught 19 and Reticle 18.
Not looked at: nothing
Next: you read Details, then decide on Reticle.
Found
- Second Eye caught 19 of 23 broken versions (evals/results/2026-10-01-c98ad5-seeded/run.json:1)
- Reticle's tests of asked outcomes caught 18 of 23; 20 had a test (evals/results/2026-10-01-c98ad5-seeded/run.json:1)
- on the 11 real fixes: Second Eye 2 false alarms, Reticle 0 (evals/results/2026-10-01-c98ad5-seeded/run.json:1)
Details
- Run c98ad5 on parallax 08a0dd3, budget $2.00, $0.77 spent.
- Second Eye alone, scored on findings about behavior. Each case's versions, intent and Reticle results come from the seeded run named on its line.
- pathspec-77: undo the hunk at pathspec/patterns/gitwildmatch.py:316: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right. $0.05. From run 9a3432.
- tomlkit-430: flip == to != at tomlkit/api.py:266: Second Eye catch, Reticle catch; change 1 to 2 at tomlkit/api.py:266: Second Eye catch, Reticle catch; change 0 to 1 at tomlkit/api.py:267: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right. $0.06. From run 9a3432.
- tomlkit-512: undo the hunk at tomlkit/items.py:2120: Second Eye catch, Reticle catch; undo the hunk at tomlkit/items.py:2138: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right. $0.07. From run 9a3432.
- cachetools-387: no broken version failed the hidden tests. Real fix: Second Eye right, Reticle right. $0.02. From run 9a3432.
- tabulate-180: change 0 to 1 at tabulate/__init__.py:1507: Second Eye catch, Reticle no test; undo the hunk at tabulate/__init__.py:1506: Second Eye miss, Reticle no test; undo the hunk at tabulate/__init__.py:2069: Second Eye miss, Reticle no test. Real fix: Second Eye right, Reticle no test. $0.07. From run 9a3432.
- tabulate-190: flip != to == at tabulate/__init__.py:1521: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right. $0.04. From run 9a3432.
- tabulate-231: flip == to != at tabulate/__init__.py:105: Second Eye catch, Reticle catch; change 1 to 2 at tabulate/__init__.py:111: Second Eye catch, Reticle catch; undo the hunk at tabulate/__init__.py:104: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right. $0.08. From run 9a3432.
- boltons-319: change 12 to 13 at boltons/timeutils.py:381: Second Eye catch, Reticle catch; change 1 to 2 at boltons/timeutils.py:394: Second Eye catch, Reticle catch; undo the hunk at boltons/timeutils.py:393: Second Eye miss, Reticle catch. Real fix: Second Eye right, Reticle right. $0.09. From run 9a3432.
- boltons-337: undo the hunk at boltons/dictutils.py:74: Second Eye catch, Reticle catch; undo the hunk at boltons/dictutils.py:178: Second Eye catch, Reticle catch; undo the hunk at boltons/dictutils.py:194: Second Eye miss, Reticle miss. Real fix: Second Eye false alarm, Reticle right. $0.13. From run 9a3432.
- boltons-348: undo the hunk at boltons/cacheutils.py:240: Second Eye catch, Reticle catch. Real fix: Second Eye right, Reticle right. $0.05. From run 9a3432.
- humanize-174: flip == to != at src/humanize/time.py:162: Second Eye catch, Reticle catch; change 0 to 1 at src/humanize/time.py:162: Second Eye catch, Reticle catch; flip < to <= at src/humanize/time.py:162: Second Eye catch, Reticle miss. Real fix: Second Eye false alarm, Reticle right. $0.12. From run 9a3432.

