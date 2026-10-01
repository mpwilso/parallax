Bottom line: Accept and merge will land a task whose base has moved, by merging master into the task in a throwaway worktree and re-running the test gate on the result, so the click no longer fails with "has moved on".
Not looked at: tests/test_ui_server.py beyond one grep hit, README.md and docs/PRODUCT.md beyond grepped lines, the test_accept.py cases beyond grepped lines, parallax/ui.py beyond its comment, how scripts/test.sh is run today, and how the policy's `[merge] test_command` is set in this repo.

## Steps
1. `parallax/accept.py`, `merge_now`: replace the refusal at lines 174-176. When HEAD is an ancestor of the accepted commit, take the fast-forward path unchanged (outcome 1). When it is not, take a new moved-base path.
2. `parallax/accept.py`: add a helper, `merge_base_in(project, d)`, for the moved-base path. It adds a detached throwaway worktree at the accepted commit, outside the repo like `run_tests` does. It runs a real `git merge --no-ff --no-edit <current HEAD sha>` there, with a message naming the task and the base branch. It is not a rebase or cherry-pick. On a conflict it runs `git merge --abort`, removes the worktree, and raises `ParallaxError` with one line that names the conflicting files and says the target branch didn't move (outcome 5). On success it returns the new merge commit sha. The worktree is removed in a `finally`.
3. `parallax/accept.py`, `merge_now`: run the existing test gate (`project.policy.merge["test_command"]`, `scripts/test.sh` here) through `runner or TEST_RUNNER` on the merge commit, not the accepted commit. This is the only check. A failure raises the existing "its tests failed" error and moves nothing (outcome 6). The `merge.tested` ledger entry records the merge commit.
4. `parallax/accept.py`, `merge_now`: only after the gate passes, point the task branch at the merge commit with `update-ref`. Then run `git merge --ff-only` of the branch into the checkout. That is a fast-forward because the merge commit has HEAD as a parent. Nothing is pushed, and it needs no second confirmation (outcome 4). Log `merge.clicked` with a note saying whether it was a fast-forward or master was merged in. Return a message that says which path ran.
5. `parallax/accept.py`: leave `confirm_merges` (line 217) as is. A landed merge commit has the accepted commit as an ancestor, so the task shows as merged (outcomes 7 and 8). Update the module docstring and the `merge_now` docstring to describe both paths.
6. `parallax/ui.py`, line 126: update the comment to say fast-forward when the base is the tip, otherwise merge master in and re-run the gate. Check whether the handler's response or error text also assumes fast-forward only.
7. `parallax/web/app.js`, line 12: change the `merge` button text to cover both paths, for example "accepts, then lands it on your base branch here: a fast-forward, or master merged in first if it moved, with the tests re-run; never forces, never pushes".
8. `README.md` and `docs/PRODUCT.md` (lines 21 and 34): reword the Accept and merge text to describe both paths. Keep "never pushed" and "only on your click".
9. `tests/test_accept.py`: change the existing moved-base test near line 223, which expects "can't fast-forward". Add the new tests listed below.
10. `tests/test_ui_server.py`: add a test that posts the merge click with a moved base and checks the landed message. Keep the existing test at line 153.

No new domains, outside reads, binaries, symlinks or dependencies are needed.

## Tests
New tests first, all using the injected `runner` so no real suite runs:
- `tests/test_accept.py`, moved base lands: the base gains a non-conflicting commit after the task starts. After Accept and merge, master holds a merge commit with both parents, the runner was called once on that merge commit, and the task is confirmed merged.
- `tests/test_accept.py`, conflict: master and the task change the same line. The call raises, master's sha is unchanged, the task branch is unchanged, no throwaway worktree is left, and the runner was never called.
- `tests/test_accept.py`, gate fails after the merge: the runner returns a nonzero exit. The call raises with the first failure, and master and the task branch are unchanged.
- `tests/test_accept.py`, merged-rule guard: a cherry-picked or rebased copy of the task is not reported merged.
- `tests/test_accept.py`, existing fast-forward tests still pass unchanged (outcome 1).
- `tests/test_ui_server.py`, moved base through the server: the merge click returns a landed message and master moves. A conflict returns the conflict line.

## Risks
- The throwaway worktree could be left behind on a crash. The `finally` cleanup and a test cover this.
- A merge can pull in a docs/tasks path clash. Accept moves the task folder out of the checkout, so the final fast-forward should be safe, but a test should check it.
- Updating the task branch before the final fast-forward could leave it ahead if the fast-forward then fails, for example when the checkout is dirty. It is only a local branch, and the error says so.
- A gate that takes long holds the click. This is already true today.
- Existing tests that check the old "can't fast-forward" wording will need updates.
- A signing key is set only for the accept commit. The merge commit may be unsigned, and I haven't decided whether to sign it. I would follow `signing_key` if it's cheap.

```toml
files = ["parallax/accept.py", "parallax/ui.py", "parallax/web/app.js", "tests/test_accept.py", "tests/test_ui_server.py", "README.md", "docs/PRODUCT.md"]
tests = ["tests/test_accept.py", "tests/test_ui_server.py"]
lines_changed = 220
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = ""
estimated_cost_usd = 2.00
budget_cap_usd = 5.00
covers = { "1" = ["tests/test_accept.py"], "2" = ["tests/test_accept.py", "tests/test_ui_server.py"], "3" = ["tests/test_accept.py"], "4" = ["tests/test_accept.py", "tests/test_ui_server.py"], "5" = ["tests/test_accept.py", "tests/test_ui_server.py"], "6" = ["tests/test_accept.py"], "7" = ["tests/test_accept.py"], "8" = ["tests/test_accept.py"], "9" = ["Steps 5, 6, 7, 8"], "10" = ["tests/test_accept.py", "tests/test_ui_server.py"] }
user_flows = []
```
