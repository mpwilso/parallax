Type: FYI
Bottom line: Of 13 broken versions, Second Eye caught 13 and Reticle 11.
Not looked at: boltons-319, boltons-337, boltons-348, humanize-174: the run stopped before them
Next: you read Details, then decide on Reticle.
Found
- Second Eye caught 13 of 13 broken versions (evals/results/2026-09-30-9ba608-seeded/run.json:1)
- Reticle's tests of asked outcomes caught 11 of 13; 12 had a test (evals/results/2026-09-30-9ba608-seeded/run.json:1)
- with its inferred-outcome tests too, Reticle caught 12 of 13 (evals/results/2026-09-30-9ba608-seeded/run.json:1)
- on the 7 real fixes: Second Eye 7 false alarms, Reticle 1, and 3 inferred-outcome notes (evals/results/2026-09-30-9ba608-seeded/run.json:1)
Details
- Run 9ba608 on parallax 6311391, budget $3.50, $1.12 spent.
- pathspec-77: undo the hunk at pathspec/patterns/gitwildmatch.py:316: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 1 notes. $0.11.
- tomlkit-430: flip == to != at tomlkit/api.py:266: Second Eye catch, Reticle catch; change 1 to 2 at tomlkit/api.py:266: Second Eye catch, Reticle catch; change 0 to 1 at tomlkit/api.py:267: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 0 notes. $0.13.
- tomlkit-512: undo the hunk at tomlkit/items.py:2120: Second Eye catch, Reticle catch; undo the hunk at tomlkit/items.py:2138: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 0 notes. $0.13.
- cachetools-387: no broken version failed the hidden tests. Real fix: Second Eye false alarm, Reticle right, 0 notes. $0.12.
- tabulate-180: change 0 to 1 at tabulate/__init__.py:1507: Second Eye catch, Reticle miss; undo the hunk at tabulate/__init__.py:1506: Second Eye catch, Reticle catch; undo the hunk at tabulate/__init__.py:2069: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle right, 1 notes. $0.14.
- tabulate-190: flip != to == at tabulate/__init__.py:1521: Second Eye catch, Reticle no test. Real fix: Second Eye false alarm, Reticle no test, 0 notes. $0.11.
- tabulate-231: flip == to != at tabulate/__init__.py:105: Second Eye catch, Reticle catch; change 1 to 2 at tabulate/__init__.py:111: Second Eye catch, Reticle catch; undo the hunk at tabulate/__init__.py:104: Second Eye catch, Reticle catch. Real fix: Second Eye false alarm, Reticle false alarm, 1 notes. $0.27.

