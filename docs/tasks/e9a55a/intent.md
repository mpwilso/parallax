Bottom line: Fix the five broken steps in the README's WSL2 install section so a fresh distro ends up working, and add a test that pins the README's `parallax doctor` sample block to what `parallax doctor` actually prints.
Not looked at: I did not run the install steps on a fresh WSL2 distro, so the fixes are read from the text and from what the code expects. I did not check the Node version Ubuntu 24.04's apt currently ships, or the minimum version `@anthropic-ai/sandbox-runtime` states.

kind: docs
size: small
title: fixing the five WSL install steps in the README

## Problem

The Windows install walkthrough in `README.md:40` has five faults. Each one stops a reader who follows the steps in order.

1. No way to become the new user. `README.md:50` creates the user with `adduser <you>` and `usermod -aG sudo <you>`, then `README.md:56` says "Then as your user" without ever switching. The reader is still root. `su - <you>` is missing.
2. `uv` and `claude` are not on PATH yet. `README.md:56` installs both with their own scripts, which write `~/.local/bin/env` and leave the current shell unchanged. The next command, `uv tool install` at `README.md:60`, fails with "uv: command not found". `source ~/.local/bin/env` is missing between them.
3. Node from apt is too old. `README.md:53` installs `nodejs npm` from apt, which on Ubuntu 24.04 is Node 18, and `README.md:54` then installs `@anthropic-ai/sandbox-runtime` on top of it. The sandbox runtime needs a newer Node, so the install or the first sandbox launch fails. The step needs to remove apt's `nodejs` and `npm`, add the NodeSource `setup_22.x` repository, and install `nodejs` from there.
4. The clone's `origin` points into `/mnt/c`. `README.md:59` clones from `/mnt/c/<path to parallax>`, so `origin` is a `/mnt/c` path. Step 6 at `README.md:63` then turns Windows drives off, following `README.md:88`. After that every `git fetch` and `git pull` in the clone fails, because the remote path no longer exists. The step must drop or repoint `origin` after the clone, while the drives are still mounted.
5. `safe.directory` is never mentioned. The repo has no reference to it anywhere. A clone made across `/mnt/c` can land with ownership git refuses to trust, and git then rejects commands in that repo. Both the repo path and its `.git` path need a `safe.directory` entry, since the check trips on each separately.

Separately, the `parallax doctor` sample at `README.md:74` is hand written and nothing checks it. It happens to match `report()` in `parallax/doctor.py:158` today, including the column widths that `report()` computes from the longest detail, and the `ready.` line printed by `_doctor()` in `parallax/cli.py:346`. Nothing stops a later change to a check name, a detail string, or the padding from leaving the README silently wrong. `tests/test_m7.py:105` tests `report()` output, but never reads the README.

## Outcome

- The Windows walkthrough in `README.md` contains all five fixes: a `su - <you>` step, a `source ~/.local/bin/env` step before the first use of `uv` or `claude`, a Node step that removes apt's `nodejs` and `npm` and installs Node 22 from NodeSource, an `origin` fix in the clone step done while drives are still mounted, and `git config --global --add safe.directory` for both the repo path and its `.git` path.
- The steps stay in an order a reader can follow top to bottom as one shell session, with the switch to the unprivileged user in the right place, and with every command run as the user that can run it.
- A test reads the fenced `parallax doctor` sample out of `README.md` and asserts it equals `report()` plus the ready line, for a healthy hardened WSL2 machine built the way `tests/test_m7.py:18` builds one. The approval key line in the sample is the existing-key wording, not the `created ...` wording, so the test arranges an existing key rather than editing the sample.
- The test fails if a check name, a detail string, the column padding, the line order, or the `ready.` line changes without the README changing.
- The full test suite passes.

## Constraints

- Do not change any behavior in `parallax/doctor.py` or `parallax/cli.py`. This task edits documentation and adds a test. If the README sample and the real output disagree, the README moves.
- Do not change the existing `parallax doctor` sample's content to make the test pass. The sample must stay a true copy of the real output; fix the test setup instead.
- Keep the macOS and Linux paragraph at `README.md:66` correct after the Windows steps are renumbered or reordered, including its reference to "step 5".
- Keep the `#harden-wsl` anchor and the links to it intact. `parallax/doctor.py:26` points at `README.md#harden-wsl`, and moving or renaming that heading breaks the hint.
- Do not weaken the hardening advice. Drives and interop still end up off; the install steps work around that instead of leaving drives mounted.
- Do not touch other README sections, `docs/plan.md`, or the milestone list at `README.md:178`.
- Do not add a dependency, a fixture file, or a network call to the test suite. The new test reads the repo's own `README.md` from disk.
- The whole task, including any rework, fits a 4.00 USD cap.
