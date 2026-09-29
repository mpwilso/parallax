# Parallax

**Agents do the work. You make the calls.**

Parallax is a small orchestration layer for AI coding agents built around one idea: the scarce resource isn't execution, it's judgment. Agents can already write a lot of code. What gets lost is who decided what, on what evidence, and whether anyone actually checked.

## The problem

Most agent tooling is optimized for throughput: more agents, more tasks, more diffs. That works until something ships that nobody really decided to ship. The other extreme asks the human about everything, which turns review into a rubber stamp.

Parallax aims for the middle: a human decides at a few points that matter, and agents work freely inside a sandbox everywhere else.

## Core ideas

- **Deny by default.** Anything that crosses the sandbox's boundary is refused unless you approved it. Nothing is allowed because nobody thought to forbid it.
- **Two vantage points.** A maker does the work. A checker reviews it without ever seeing the maker's explanation, only the task and the diff. If they disagree, it goes to a human. Disagreements are never auto-resolved.
- **Honest empty states.** "No finding" is a real answer, not a failure to fill the page.
- **Everything is on the record.** Grants, refusals, verdicts, disagreements, and human decisions go into an append-only ledger, with reasons.

## How it works

1. You create a task. It gets its own branch and git worktree.
2. A maker agent works the task within the policy.
3. Any action the policy doesn't allow becomes a pending decision in your inbox.
4. A blind checker reviews the diff.
5. Agreement moves the task forward. Disagreement goes to your inbox.
6. Merging is always a human decision.
7. Every step is logged.

Parallax is being restructured around intent, plan, build, check, and accept. See [docs/plan.md](docs/plan.md).

## Status

Early. The engine, a command-line inbox, and a visual inbox work. The restructure is in progress.

## Install

Parallax runs makers inside Claude Code's sandbox, which needs macOS, Linux, or WSL2. **On Windows, run Parallax inside WSL2**, in a distro just for it. Native Windows isn't supported.

Setup on Windows, once, about 30 minutes:

1. In PowerShell as admin: `wsl --install --no-distribution`, then restart.
2. In PowerShell, make a distro named `parallax`:
   ```powershell
   wsl --install Ubuntu-24.04 --no-launch
   wsl --export Ubuntu-24.04 $env:TEMP\u.tar
   wsl --import parallax C:\WSL\parallax $env:TEMP\u.tar --version 2
   wsl --unregister Ubuntu-24.04
   ```
3. `wsl -d parallax`, then as root create your user: `adduser <you>` and `usermod -aG sudo <you>`.
4. Install the tools, as root. Apt's Node is too old for the sandbox runtime, so take Node from NodeSource:
   ```bash
   apt update && apt install -y git bubblewrap socat ripgrep python3 curl ca-certificates
   apt remove -y nodejs npm
   curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
   apt install -y nodejs
   npm install -g @anthropic-ai/sandbox-runtime
   ```
5. Become your user and install uv and Claude Code. Each installer writes `~/.local/bin/env` and leaves the running shell's PATH alone, so source it before using either:
   ```bash
   su - <you>
   curl -LsSf https://astral.sh/uv/install.sh | sh
   curl -fsSL https://claude.ai/install.sh | bash
   source ~/.local/bin/env
   claude                                        # log in
   ```
6. As your user, while Windows drives are still mounted, clone Parallax into the WSL filesystem and install it:
   ```bash
   git config --global --add safe.directory /mnt/c/<path to parallax>
   git config --global --add safe.directory /mnt/c/<path to parallax>/.git
   git clone /mnt/c/<path to parallax> ~/code/parallax
   git config --global --unset safe.directory /mnt/c/<path to parallax>/.git
   git config --global --unset safe.directory /mnt/c/<path to parallax>
   git -C ~/code/parallax remote remove origin   # it points into /mnt/c, which step 7 unmounts
   uv tool install --editable "$HOME/code/parallax[claude]"
   ```
   Git won't touch a `/mnt/c` repo it doesn't trust, and it checks the repo path and its `.git` path separately, which is why both lines are there and why they come before the clone; the new copy in `~/code/parallax` is yours, so the exception goes away again right after. If you have a GitHub remote, set that as `origin` instead of removing it. Optional: set up an SSH signing key, so accept commits are signed.
7. Harden the distro. See [Harden WSL](#harden-wsl).
8. Start Claude Code, and run Parallax, from inside the distro.

On macOS or Linux, skip the WSL steps: install git, socat and bubblewrap (Linux only), `@anthropic-ai/sandbox-runtime`, uv and Claude Code, then run step 6 with your own clone. The `/mnt/c` parts of that step — the two `safe.directory` lines and the `origin` removal — are Windows only.

Then check the machine:

```bash
parallax doctor
```

```
platform      linux on wsl2                      ok
sandbox       bubblewrap, socat, srt             ok
claude login  found                              ok
windows       interop off, path off, drives off  ok
approval key  ~/.config/parallax/key             ok
signing key   none: accept commits won't be signed
ready.
```

A missing sandbox tool, a missing Claude login, or the wrong platform is a failure. Windows interop, the Windows PATH, or mounted Windows drives left on is a warning. `doctor` creates the approval key if it's missing; only you can read it.

### Harden WSL

With interop on, anything in the distro can start Windows programs. With drives mounted, it can read and write your Windows files. Turn both off. Write `/etc/wsl.conf`:

```
[user]
default=<you>

[interop]
enabled=false
appendWindowsPath=false

[automount]
enabled=false
```

Then in PowerShell run `wsl --terminate parallax`, wait 8 seconds, and open the distro again. `parallax doctor` should show `interop off, path off, drives off`. The settings are described in Microsoft's [wsl.conf reference](https://learn.microsoft.com/windows/wsl/wsl-config).

**Networking:** the default NAT mode is enough. A Windows browser reaches a server bound to 127.0.0.1 in WSL through `localhost`. With interop off, WSL can't open your browser, so `parallax ui --no-open` prints the link for you to open.

## Quick start

In the folder of a git repo you want agents to work on:

```bash
parallax init                                  # writes parallax.policy.toml, REVIEW.md and .parallax/
parallax do "the install steps are wrong for WSL"   # describe the work once; it runs without you
parallax inbox                                 # what waits on you: one item per task
parallax show <task-id>                        # its card: the work, what changed, tests, the checker, risks
parallax diff <task-id>                        # the change itself
parallax accept <task-id>                      # commit exactly what was reviewed; it prints the merge for you to run
parallax reject <task-id> --reason "..."       # or send it back with your reason
parallax stats                                 # human touches per task; the target is 1
```

That's the whole loop for a normal small task: you describe it, and you accept or reject it. In between, without you: drafters write the intent and plan to `docs/tasks/<id>/`; Parallax normalizes and lints them and checks the plan against the intent by code (every file inside the intent's scope, every outcome covered, the budget right), sending any problem back to the drafter up to 2 times; then the policy's launch rule decides. A small task whose budget cap is at most `auto_launch_usd` (default $3) and that touches nothing in `review_paths` launches on its own; the approval is signed and names the rule. A large task, a plan touching `review_paths`, or `review_plans = true` waits for you: `parallax approve <task-id>` starts it.

Power use: `parallax stop` ends every running task, and `preflight`, `build`, `recheck`, `lint`, `log` and `verify` are there when you need them.

`parallax build` runs preflight first and refuses to launch if either layer would let a protected path be written. The maker runs in Claude Code's sandbox with rules generated from the plan: writes only in the task's worktree (never the protected files), no reads of your home folder except the worktree and the task's venv, and no network unless the plan names the domains. Nothing waits on you mid-build: an action that needs your approval is refused and recorded. The maker never commits. The build's log is in `~/.local/share/parallax/tasks/`.

Then the check runs, in the same background process. Code checks the diff against the plan first: files outside it, unlisted binaries or symlinks, new dependencies, and a diff over 400 changed lines all come to you. Parallax runs the plan's tests itself, in the sandbox, with the test harness (conftest, pytest settings) from the base branch. The blind checker, a different Claude model, sees only the intent's outcome and constraints, `REVIEW.md`, and the diff. If tests fail or the checker finds something `REVIEW.md` says blocks, the maker gets the findings and reworks, up to 3 times; the fail after that comes to you. A finding that sets your intent against your approved plan never goes to the maker: it comes to you. Hitting the budget cap, which covers the whole task from its first draft, stops the task and brings it to you too. `parallax show <task-id>` says where it stands.

If your tests need packages, set `[build] setup` in the policy file to a command that makes the task's venv at `$PARALLAX_VENV`. It runs as you, before the build. For example:

```toml
[build]
setup = 'uv venv -q --python /usr/bin/python3 "$PARALLAX_VENV" && uv pip install -q --link-mode copy --python "$PARALLAX_VENV" pytest'
```

At Ready, `parallax accept <task-id>` checks the approved files against their hashes, scans the change for secrets, writes `docs/tasks/<id>/record.md` from the ledger, and commits exactly the reviewed tree plus `docs/tasks/<id>/` on the task's branch, with no hooks and with trailers (`Parallax-Task`, `Approved-By`, `Verified-By`, `Ledger-Head`). It's signed if you've set `user.signingkey`. Then it prints the merge command. Merging is yours; your next command notices the merge and records it. What this does and doesn't protect is in [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

`parallax task new "..."` and `parallax run <task-id>` still run a task the old way, straight from its goal. How the lifecycle, the output shape and the principles fit together is in [docs/parallax.md](docs/parallax.md).

Worktrees live outside your repo, in `~/.local/share/parallax/worktrees/`.

## Deciding in your browser

```bash
parallax ui --no-open
```

This opens every decision waiting on you in a local page. Items are on the left; click one to see what you need to decide it: the change as a colored diff, the checker's findings, the refusals that got a task stuck. Write a reason and click one of two plain choices ("Allow" / "Refuse", "Side with the maker" / "Side with the checker", and so on). New items appear on their own while agents work, the tab shows how many are waiting, and you can turn on a desktop notification for new items. The Tasks tab shows every task with its status, cost, timeline and change, and for a ready task, the exact commands to merge it yourself.

The page runs on your machine only. Every decision still needs a reason and is recorded as yours, and no agent can reach the page to make one.

## Maker and blind checker (Milestone 2)

```bash
parallax run <task-id>             # maker works the task, then the blind checker reviews the diff
parallax inbox                     # permission requests and maker/checker disagreements
parallax approve <id> --reason ".." # on a disagreement: side with the maker
parallax reject <id> --reason ".."  # on a disagreement: side with the checker
```

While the maker runs, nothing waits on you: an `ask` action is refused and recorded. The checker sees only the task goal and the diff: never the maker's summary, its plan, or commit messages. `pass` and `no_finding` count as agreement and the task becomes `ready`. Anything else goes to your inbox. Agents can't write the protected paths (CLAUDE.md, `.claude/`, `.git`, `docs/tasks/`, the policy file and the rest), whatever the policy says.

Limits live in the policy file under `[limits]`: `max_parallel` (default 4), `stuck_after` (the same call refused this many times stops the maker and puts it in your inbox, default 3), and `stale_minutes` (a running task silent this long is flagged stuck on your next command, default 60). A task can't create tasks or resolve decisions.

## Roadmap

- [x] M1: policy file, ledger, single task in an isolated worktree
- [x] M2: maker and blind checker, plan review, verdicts, disagreement routing
- [x] M3: conductor on a pulse, driven by a human-owned mission file, several tasks in parallel
- [x] M4: evidence moves the line both ways: promotion proposals and law proposals, each approved by a human
- [x] M5: evals against real open-source changes, results published
- [x] M6: local visual decision inbox
- [x] M7: move to WSL2, cut the conductor, pulse, profiles, promotions and laws, add `parallax doctor`
- [x] M8: intent, spec, plan, and the gates. `parallax lint` and the output shape
- [x] M9: the sandboxed build, preflight, and `parallax stop`
- [x] M10: the check: code checks, the plan's tests in the sandbox, the blind checker, and rework
- [x] M11: accept, the implementation record, and the threat model
- [ ] M12 to M14: see [docs/plan.md](docs/plan.md)

## Evals

Parallax is tested against already-merged open-source fixes: it gets the issue text only, works it the way it would work anything, and is scored by the tests the humans added in their pull request, which it never sees. Results are published in [evals/results/](evals/results/), failures included.

What's measured besides "did it work": how often the blind checker caught a bad fix, how often it missed one (a wrong change that would have reached you marked ready), how often it raised a false alarm, and how many items would have landed in your inbox.

```bash
parallax eval check     # every case is sound: the PR's tests fail before the fix and pass after. no model, no cost
parallax eval run       # run all cases, stops at --budget (default $25), writes a report
parallax eval report    # rebuild the latest report
```

**Latest results** (2026-09-28, 11 cases from 6 projects, $12.12 in all):

- **10 of 11 fixes resolved.** Run [67faab](evals/results/2026-09-28-67faab.md) resolved 8. Two more crashed on a bug in Parallax itself: diffs with non-ASCII text broke on Windows. That's fixed, and both resolved on rerun [73982e](evals/results/2026-09-28-73982e.md).
- **The blind checker:** 8 right, 1 missed, 1 false alarm. The miss (humanize-174) passed all 665 existing tests but not the humans' rounding rule, so it would have reached you marked ready. The false alarm (tomlkit-512) flagged a correct fix. One case finished at its budget cap before the checker ran.

Cases live in [evals/cases.toml](evals/cases.toml). Evals run under a fixed policy with no asks, so nothing waits on a human and no model approves anything.

## Non-goals

- Replacing human review
- Running unsupervised against production
- Being a general agent framework or an IDE
