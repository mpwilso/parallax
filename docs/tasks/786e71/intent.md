Bottom line: Accept and merge will land a task whose base branch has moved, by merging master into the task and re-running the test gate, so the click no longer fails with "has moved on".
Not looked at: tests/test_accept.py, tests/test_ui_server.py, README.md, docs/PRODUCT.md, and how scripts/test.sh is run today (only grepped accept.py, ui.py and app.js).

kind: feature
size: small
title: letting Accept and merge land tasks whose base has moved
scope: parallax/accept.py, parallax/ui.py, parallax/web/app.js, tests/test_accept.py, tests/test_ui_server.py, README.md, docs/PRODUCT.md
budget: 5.00

## Problem
Accept and merge only fast-forwards. In `parallax/accept.py:174-176` it refuses when the base branch is no longer an ancestor of the accepted commit, with the error "can't fast-forward ... has moved on since the task began". The merge itself runs `git merge --ff-only` at `parallax/accept.py:189`. So a task whose base has moved cannot be landed from Parallax. The module doc at `parallax/accept.py:12`, the click handler comment at `parallax/ui.py:126` and the button text at `parallax/web/app.js:12` all describe fast-forward only. The "merged" check at `parallax/accept.py:217` treats a task as merged when its accepted commit is an ancestor of the branch head, and that rule is correct and stays.

## Outcome
1. asked: When the task's base is still the tip of master, Accept and merge fast-forwards exactly as it does today.
2. asked: When the base has moved, Accept and merge merges master into the task. It is a real merge, not a rebase or cherry-pick.
3. asked: After master is merged into the task, Accept and merge re-runs the pre-merge test gate, `scripts/test.sh`, and no other check. There is no blind review or any other step.
4. asked: If the gate passes, the task lands on that first click, with no second confirmation.
5. asked: If merging master into the task hits a conflict, nothing is merged and master is left unchanged. Parallax tells the user about the conflict.
6. asked: If the gate fails after master is merged in, nothing is merged and master is left unchanged.
7. asked: A task landed this way shows as merged, because its accepted commit is then an ancestor of master.
8. asked: The rule that a task counts as merged when its accepted commit is reachable from master is kept. It is not extended to rebased or cherry-picked tasks.
9. inferred: The Accept and merge button text in `parallax/web/app.js`, the comment in `parallax/ui.py`, and the docs in README.md and docs/PRODUCT.md describe both paths (fast-forward when the base is the tip, merge master in when it has moved).
10. inferred: Tests in tests/test_accept.py and tests/test_ui_server.py cover the fast-forward path, the moved-base landing, the conflict case, and the gate-failure case.

## Constraints
- Nothing is ever pushed. The merge stays local and only happens on the user's click.
- The fast-forward path behaves as it does today.
- The merged-state rule at `parallax/accept.py:217` is unchanged.
- No check runs other than `scripts/test.sh`.
- Changes stay inside the listed scope. Budget is $5.
