# One task, start to finish

Task `e9a55a` was done on this repo by Parallax, and merged. Every file it names is in the repo and every ledger id is in `.parallax/ledger.jsonl` on the machine that ran it; the commit is in this branch's history. This is what one task looks like when it works, including the one place a person stepped in.

It ran before the hands-free redesign, when every plan waited for a person. The later runs through the UI in [ui-runs](ui-runs/README.md) took one touch (run 1, `1-no-touches`: accept) or two (run 2, `2-ui-change`: a launch confirm and accept; run 3, `3-reject-redraft`: a reject and accept).

## 1. The request, typed in

The whole input was one paragraph in the intake box (ledger `26bf1e3d`, 00:03):

> Fix all five WSL install problems in the README: no su - <you> after adduser; uv and claude not found until source ~/.local/bin/env; apt's Node 18 too old for sandbox-runtime, use NodeSource setup_22.x after removing apt nodejs and npm; clone origin points at /mnt/c and breaks after drives are off; safe.directory needed for both the repo path and its .git path. Add a test that pins the README's doctor sample to parallax doctor. Budget cap 4.00.

## 2. Focus (which drafts the intent and plan) writes both

Focus read the repo and wrote [intent.md](tasks/e9a55a/intent.md) (ledger `bbc8fa37`, $0.36): a problem statement citing `README.md` by line, five numbered outcomes, constraints (don't change `doctor.py` or `cli.py`, keep the `#harden-wsl` anchor, a $4.00 cap) and a `scope` of the two files the work may touch. Focus then wrote [plan.md](tasks/e9a55a/plan.md) (ledger `ff8a37df`, $0.44): steps by file and line, the tests that prove it, risks, and the `toml` block code reads: `files`, `tests`, `covers`, no domains, no outside reads, an estimate of $2.60 under the $4.00 cap.

Code checked the plan against the intent before anyone saw it: every listed file inside the intent's scope, every outcome covered, the cap matching the budget the request named.

## 3. The human touches

This task ran before the launch rule existed, so its plan waited for a person. The first plan was rejected with a reason (ledger `8bf092b7`, 00:08):

> Two plan errors. (1) safe.directory must come before the clone and name the SOURCE: /mnt/c/<path to parallax> and /mnt/c/<path to parallax>/.git. Git refuses the /mnt/c source during the clone; the new copy in ~/code/parallax never needs it. Unset both right after the clone. (2) budget_cap_usd must be 4.00, as the intent says. Everything else stays.

Focus got that reason and the intent, nothing else, and wrote the plan again (ledger `549ee785`, $0.42). Both drafted files were then edited by hand before the approval (ledger `4cbfefd2`, 00:20), which is why their recorded hashes differ from the drafts'; `parallax stats` counts each hand edit as a touch, and today the rule is to reject with a reason instead. The approval hashed both files and signed them with the key the sandbox can't read.

## 4. Maker builds, in the sandbox

Setup made the task's venv as you, on a fresh copy of the base commit (ledger `e8962932`). Preflight tried to write every protected path, read the approval key and reach the network with the build's own rules, and got nowhere (ledger `22e666ef`: "0 of 11 protected paths writable; 0 of 57 protected writes allowed"). Maker then ran for two minutes with the approved intent and plan as its brief and no route to your home folder, keys or network (ledger `c9abb918`, $0.71). It changed `README.md` and added one test in the doctor tests.

## 5. The check

Parallax staged the worktree into a throwaway index, recorded the tree hash, and confirmed by code that only the plan's two files changed. It ran the plan's tests itself, in the sandbox, with the test harness taken from the base commit: 22 of 22 passed (ledger `9251b1ac`). Second Eye, a different model with no tools, got exactly the intent's outcome and constraints, `REVIEW.md` and the diff, and said pass with three findings, none blocking, and listed what it couldn't see from the diff alone (ledger `7012aef9`, $0.05). The task went to Ready (ledger `eb1f1024`).

## 6. Accept, and the merge that stays yours

`parallax accept e9a55a` (ledger `c598e411`, 00:25) re-checked the approved files against their hashes, ran the secrets scan (ledger `e5c7cedd`), wrote [record.md](tasks/e9a55a/record.md) from the ledger, and committed exactly the reviewed tree plus `docs/tasks/e9a55a/` with `git commit-tree`, so no hook ran. The commit message carries the trailers:

```
Parallax-Task: e9a55a
Intent: docs/tasks/e9a55a/intent.md
Approved-By: Matt Wilson (intent+plan) 2026-09-29T00:20Z
Verified-By: checker=pass tests=22/22
Ledger-Head: 1696dd5a...
```

`git log --grep 'Parallax-Task: e9a55a'` finds it. The merge was run by hand, and the next command confirmed the commit landed unchanged (ledger `f6906f02`).

## What it cost

An estimated $1.98 of the $4.00 cap: drafting $1.22 (three drafts, one after the reject), the build $0.71, the check $0.05. Twenty-two minutes from the request to accept, of which the plan waited twelve for a person. `parallax stats` counts it as five touches: the reject, the two hand edits, the approval and the accept. A task the launch rule can start on its own takes one, the accept, and the three runs in [ui-runs](ui-runs/README.md) show that.
