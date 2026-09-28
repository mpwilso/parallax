**Type:** FYI
**Bottom line:** Eight milestones rebuild Parallax around intent, plan, build, check, and accept, core loop first.
**Not looked at:** sandbox and budget behavior in WSL2, verified in M9.
**Next:** M7 and M8 are built; M9 is next.

## Changed since last time

All six decisions are answered:

1. **Rework:** up to 3 recorded cycles. On re-review, the checker gets only its brief input plus the new diff, never the maker's reply to its findings.
2. **Reasons:** only on rejects, overrides, and accepted risks.
3. **Cuts:** yes. Rejection reasons stay in the ledger, so laws can return later on the earned-autonomy mechanism. The principle becomes: "A correction made twice is proposed as a CLAUDE.md or REVIEW.md change; I apply it."
4. **Checker:** another Claude model, measured in the eval.
5. **Budget:** your Claude login. Every dollar figure is labeled estimated, everywhere it appears.
6. **WSL:** a dedicated distro in NAT mode. Claude Code sessions for Parallax run inside it.

## Details

This plan follows [docs/direction.md](direction.md). It was approved on 2026-09-28.

### 1. Conflicts with CLAUDE.md, and how each is resolved

CLAUDE.md is updated in M7 to match this table, and kept short.

| Current rule | Conflict with the brief | Resolution |
|---|---|---|
| 1. Anything not listed is denied, no wildcard allow | Routine work inside the sandbox is allowed without listing it. Autonomy classes use path patterns. | Restate: anything that crosses the boundary and isn't in the approved plan is refused. Path patterns only scope autonomy classes, and always exclude protected paths. |
| 3. Checker sees only the task goal and the diff | Checker now gets the intent's outcome and constraints, REVIEW.md, and the cached diff without `docs/tasks/`. | Restate to match the brief exactly. One test pins the input. |
| 4. Disagreement goes to you, never retried silently | Up to 3 rework cycles before Decision needed. | Decided: rework up to 3 cycles, each recorded. The checker's re-review input is unchanged in kind (brief input plus the new diff). The 4th fail comes to you. |
| 5. A model never approves | Earned autonomy auto-approves some plans. | Restate: a model never approves anything. Code may auto-approve a gate only under a class you approved. |
| 7. Every human decision needs a reason | Typed reasons on every approval feed approval fatigue. | Decided: reasons only on rejects, overrides, and accepted risks. |
| 9. Agents can't write the policy, mission.md, `.parallax/` | Protected list grows. | Replace with the brief's list, plus the worktree's own `.git` pointer file. |
| Code: `claude-agent-sdk` only in `parallax/agents/` | A non-Claude checker would need another vendor SDK. | Decided: another Claude model, so no new SDK. The sandbox runtime (`srt`) and git are called as programs, not imported, so the core stays stdlib. |
| Writing: CLI output is lowercase and short | Reports use the capitalized output shape. | One-line confirmations stay lowercase. Reports use the output shape. |
| State lives in the ledger, no second source | `docs/tasks/` files hold the work. | Ledger holds who decided what, plus the hash of every approved file. Files hold the work. A hash mismatch blocks accept. |
| Current milestone | Out of date. | Point at this plan. |

### 2. Map of existing features onto the new lifecycle

| Feature | Where it goes | Change |
|---|---|---|
| Policy file | Everywhere | Shrinks to: limits (parallel cap, stuck count, diff cap 400, rework cap 3, per-call drafting ceiling, default estimated caps by size), reserved rails, worktree setup command, checker model. The per-action table goes: the plan defines what may cross the boundary. |
| Ledger | Everywhere | Stays. Gains gate approvals with file hashes and an approval signature, the reviewed tree hash, test runs, accept, and clean-merge confirmation. `Ledger-Head` goes in the accept commit. Rejection reasons stay recorded. |
| Worktrees | Intent | Stay, but move out of `.parallax/` (a protected path) to `~/.local/share/parallax/worktrees/<repo>/<id>`, so the sandbox can allow writes there without touching protected paths. |
| Maker | Build | Stays on the Agent SDK. Moves into the sandbox with settings from outside the worktree, a scrubbed environment, no `git.commit`, and "ask" meaning deny and record. The PreToolUse hook stays as the tool layer. |
| Blind checker | Check | Stays tool-less. New input per the brief. Adds REVIEW.md severities and "Not looked at". Runs on a different Claude model than the maker. |
| Plan stage (plan in ledger, plan checker) | Plan | Replaced by `docs/tasks/<id>/plan.md`, approved by you. The plan checker goes: your approval replaces it. |
| mission.md and pulse | Cut | See item 3. Housekeeping (stale runs, clean-merge checks) runs on every command instead. |
| Stuck detection | Build | Stays: repeated denials stop the maker and raise Decision needed. The rework cap joins it. |
| Profiles | Cut | `readonly` investigations don't fit the lifecycle. |
| Promotions | Gates (M14) | Command-level promotions go: the build never pauses, so there's nothing to promote. The evidence and verified-writer code is reused for autonomy classes at the gates. |
| Laws | Cut for now | Rejection reasons stay in the ledger, so laws can return later on the earned-autonomy mechanism. Until then, a correction made twice is proposed as a CLAUDE.md or REVIEW.md change, and you apply it. |
| Evals | Evals (M13) | Stay. Cases move to the SWE-bench instance format. Adds about 10 cases, the test-writer (eval only), and a checker-model comparison. |
| UI | Gates and Ready (M12) | Stays. Items become gates, Decision needed, and Ready. Permission-wait items go. Approvals use the approval key. |
| Stop, cost, spawn depth, ledger lock, verify | Everywhere | Stay. Costs are labeled estimated. The inside-a-task check stays as a second layer behind the approval key. |

### 3. What's cut or simplified

From M1 to M6:
- **Conductor, `parallax goal`, proposals, recommendations.** Intent comes from you. Recommendations are extra text on every item, which feeds word soup.
- **mission.md.** CLAUDE.md and REVIEW.md cover rules and review. Fewer governance files.
- **`parallax pulse` and the queue.** Background triggers are out of scope. Approving a plan starts the build.
- **Profiles, exact command rules, command promotions, laws, `parallax evidence`, `review`, `check`, `task queue`.** None has a place in the lifecycle. Rejection reasons stay in the ledger for laws later.

From the brief, lighter versions:
- **Cost estimate:** the drafter's guess is marked Unverified until 5 similar tasks exist; then Parallax shows their median. The cap is what's enforced. Every figure is labeled estimated.
- **Changed since last time:** only on a task's Ready report after rework, where it matters.
- **Earned autonomy:** last (M14), and only after about 10 accepted tasks in daily use. By the brief's own rule it shouldn't come before the loop is used.
- **Test-writer:** eval only, as the brief says. It joins the loop only if the eval shows it catches what the checker misses.
- **Different-family checker:** a different Claude model. A second vendor only if the eval shows same-model bias.
- **Secrets scan:** built-in patterns (keys, tokens, private key blocks), called best effort in THREAT_MODEL.md. Uses gitleaks too if installed.
- **Drafters:** they get no Bash, so the tool layer is their whole boundary. No OS sandbox is needed for them.

### 4. Build order

#### Before M7: facts checked and setup

**Merge:** confirmed. `master` is at `112c66c`, the brief, on top of `b295603`. 94 tests pass.

**Settings the brief names, checked against current docs or source:**
- Sandbox runs on macOS, Linux and WSL2, not native Windows. It needs `bubblewrap` and `socat` (code.claude.com/docs/en/sandboxing).
- It covers Bash only. Read, Edit and Write go through permission rules, so both layers are needed, as the brief says.
- `sandbox.allowUnsandboxedCommands: false` removes the escape hatch, and `sandbox.failIfUnavailable` refuses to run without a sandbox.
- `sandbox.filesystem.denyRead / allowRead / allowWrite / denyWrite` and `sandbox.network.allowedDomains` exist.
- Whether sandboxed Bash can reach localhost isn't stated in the Claude Code docs. The sandbox runtime says all network is denied by default. Unverified until M9 preflight.
- Sandboxed Bash inherits the full environment by default. `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` and `sandbox.credentials` strip it.
- In SDK 0.2.160 source, `ClaudeAgentOptions` has `sandbox` and `settings` (a path or inline JSON). The typed `sandbox` lacks `filesystem`, so filesystem rules go through `settings`.
- The SDK's `env` merges over the inherited environment. So Parallax launches the maker from a scrubbed process, not only an `env` override.
- `max_budget_usd` becomes the CLI's `--max-budget-usd`. The run stops with `error_max_budget_usd` (code.claude.com/docs/en/cli-reference). The docs don't say how it behaves on a subscription login.
- Whether PreToolUse fires for Bash the sandbox auto-allows isn't stated. Parallax sets `autoAllowBashIfSandboxed: false`, so Bash passes the hook either way.
- The sandbox runtime CLI `srt` (npm `@anthropic-ai/sandbox-runtime`) runs a command under the same kind of config. Parallax uses it to run tests and preflight. Its README lists Linux, not WSL2 by name, so that's unverified until `doctor`.
- `wsl --install ... --name` isn't in Microsoft's command reference. Export and import are, so setup uses those.

**Budget:**
- **Unit:** estimated US dollars at API list prices, as the Claude CLI computes them from token use. Every figure Parallax shows is labeled estimated.
- **Enforcement:** each agent call gets `max_budget_usd` equal to what's left of the task's cap. Parallax sums every call's recorded cost in the ledger, so the cap spans drafting, build, check and rework. Before and after every agent call Parallax checks the total; at the cap the task stops and comes to you as Decision needed. Approval refuses a plan whose cap isn't above what drafting already spent, and the plan drafter is told that figure. (M9 briefly counted only costs after plan approval; that let task 003876 reach $2.52 on a $1.60 cap, and it was reverted before M11. A cap reached in the background also crashed the builder instead of reaching you; fixed at the same time.) A call can still overshoot by the turn in flight: the build that crossed 003876's cap was one call.
- **How you pay:** your Claude login, not an API key. The dollars are estimates, not charges, and the cap stops the run at that estimate. Your real limit on a subscription is the plan's usage limits, which Parallax can't see; hitting one fails the call, and the task becomes Decision needed.

**Setup (you do this once, about 30 minutes).** Virtualization is on; a hypervisor is already running.

1. In PowerShell as admin: `wsl --install --no-distribution`, then restart.
2. In PowerShell: `wsl --install Ubuntu-24.04 --no-launch`, `wsl --export Ubuntu-24.04 $env:TEMP\u.tar`, `wsl --import parallax C:\WSL\parallax $env:TEMP\u.tar --version 2`, `wsl --unregister Ubuntu-24.04`.
3. `wsl -d parallax`, then as root: create your user (`adduser matt`, `usermod -aG sudo matt`).
4. Install: `apt update && apt install -y git bubblewrap socat ripgrep nodejs npm python3 curl`. Then `npm install -g @anthropic-ai/sandbox-runtime`, and as your user, uv (`curl -LsSf https://astral.sh/uv/install.sh | sh`) and Claude Code (`curl -fsSL https://claude.ai/install.sh | bash`, then `claude` to log in).
5. As your user, while Windows drives are still mounted: `git clone /mnt/c/<path to parallax> ~/code/parallax`, and `uv tool install --editable "$HOME/code/parallax[claude]"`. The clone checks out `m7-restructure`, the branch this plan is on. Optional: an SSH signing key for accept commits.
6. Write `/etc/wsl.conf`, then run `wsl --terminate parallax` and wait 8 seconds:
   ```
   [user]
   default=matt
   [interop]
   enabled=false
   appendWindowsPath=false
   [automount]
   enabled=false
   ```
7. **Networking:** the default NAT mode is enough. A Windows browser reaches a server bound to 127.0.0.1 in WSL through `localhost` (localhostForwarding is on by default). With interop off, WSL can't open your browser, so `parallax ui` prints the link for you to open.
8. Start Claude Code in `~/code/parallax` inside the distro for M7 onward.

#### M7: move to WSL2, cut, and `parallax doctor`

- Worktrees move out of `.parallax/`. The cut features go. CLAUDE.md is updated to match item 1, including the new principle on repeated corrections. README install becomes the WSL setup.
- `parallax doctor` checks the platform, the sandbox tools, the Claude login, that interop and Windows PATH are off, and the approval key and signing key. It creates the approval key if missing.
- Tests removed, all because their feature is cut:
  - all of `test_m3_conductor.py` and `test_m4_laws.py`;
  - in `test_m3.py`: `test_profiles_are_full_tables_and_readonly_is_fixed`, `test_readonly_task_is_an_investigator`, `test_queue_and_stored_plan_flag`;
  - in `test_m4.py`: the exact-rule and command-promotion tests (`test_exact_rules_beat_action_level_and_nothing_else`, `test_details_normalize_across_worktrees`, `test_check_matches_exact_rules`, `test_promotion_needs_ten_clean_approvals`, `test_any_rejection_or_old_evidence_blocks_promotion`, `test_rejected_promotion_needs_fresh_approvals`, `test_approving_a_promotion_writes_the_policy`, `test_pulse_raises_promotions`, `test_cli_shows_promotions_and_evidence`).
- Tests changed: `test_m6.py::test_every_kind_has_its_context_and_plain_choices`, because the seed loses the cut kinds.
- **You run** `parallax doctor` in the distro. **You see:**
  ```
  platform      linux on wsl2               ok
  sandbox       bubblewrap, socat, srt      ok
  claude login  found                       ok
  windows       interop off, path off       ok
  approval key  ~/.config/parallax/key      ok
  signing key   none: accept commits won't be signed
  ready.
  ```

#### M8: intent, spec, plan, and the gates

- `parallax intent new "<rough sentences>"` creates the task, branch and worktree. Read-only drafters return the intent, then the spec (large tasks only) and the plan. Parallax writes them to `docs/tasks/<id>/`.
- The plan ends with a small `toml` block that code reads: files, tests, expected lines changed, domains, outside reads, binaries, symlinks, dependencies, REVIEW.md tightening, estimated cost, estimated budget cap.
- `parallax approve <id>` approves the pending gate: intent and plan together for small tasks, spec and plan together for large ones. It records each file's hash and signs the approval with a key in `~/.config/parallax/key`, which the sandbox can't read. A gate without a valid signature doesn't count. Approving needs no reason; rejecting, overriding, or accepting a risk does.
- The output shape, and `parallax lint`, land here. `docs/parallax.md` is written.
- Test removed: `test_m2.py::test_plan_stage_is_read_only_and_disputed_plan_blocks_build`, because the plan stage is replaced.
- **You run** `parallax intent new "the README install steps are wrong for WSL"`. **You see:**
  ```
  task 3f9a1c on branch parallax/3f9a1c-readme-install-steps
  Type: Decision needed
  Bottom line: Intent and plan are drafted for fixing the README install steps.
  Not looked at: nothing
  Next: you read docs/tasks/3f9a1c/, then run parallax approve 3f9a1c.
  ```
  Then `parallax lint docs/tasks/3f9a1c/plan.md` prints `ok`.

#### M9: the sandboxed build

- Parallax generates the sandbox and permission settings from the approved plan and writes them outside the worktree. `setting_sources=[]` means the worktree's own `.claude/` is ignored.
- The maker runs in a scrubbed process: no keys, `CLAUDE_CODE_SUBPROCESS_ENV_SCRUB` set. Reads of `$HOME` and `/mnt` are denied, except the worktree, the task's venv, and the plan's reads. Network is denied unless the plan names domains.
- "Ask" means deny and record. The maker never commits.
- **Preflight** runs before every launch:
  - The Bash layer runs `srt` with the same generated config. It tries to create new sentinel files inside each protected directory, and writes each protected file in a mirror folder generated with the same rules, so no real file is touched. It also tries to reach the UI port.
  - The tool layer calls the permission function with made-up inputs for every protected path and tool.
  - Any allowed write refuses the launch, and any sentinel that got through is removed.
- `parallax stop` ends every running agent and records it.
- **Whole process in `srt`: layer both, with the two-layer design as the floor.** The sandbox runtime can wrap the maker's whole Claude Code process (code.claude.com/docs/en/sandbox-environments), so Read, Edit, Write, hooks and MCP servers sit inside one OS boundary, not only Bash.
  - What it adds for Parallax: OS enforcement for the file tools. Today a read of `$HOME` or a write to a protected path by Read or Edit is stopped only by permission rules and our hook, so one bug there is a breach. Hooks and MCP gain nothing: our PreToolUse hook runs inside the SDK process, and the maker has no MCP servers.
  - Why it can't replace the two layers: it's a beta, and its config format may change. On Linux and WSL2 its deny list is built once at launch and doesn't cover files created later, and write grants only apply to paths that already exist. It must leave `~/.claude` and `~/.claude.json` writable and `api.anthropic.com`, `claude.ai` and `platform.claude.com` reachable, so its own boundary is wider than the Bash sandbox's.
  - So: Parallax always generates the Bash sandbox and permission rules and refuses to launch without them. It also wraps the maker in `srt` with a generated `--settings` file, which fails closed if the file doesn't load, denying writes to `~/.claude/settings.json` and other config paths. The outer layer ships only if a spike shows bubblewrap nests inside it on WSL2; if not, M9 ships the two layers and records why.
  - Preflight covers both. It adds one case: a protected file that doesn't exist yet (for example no `REVIEW.md` or `.mcp.json`) must not be creatable. Both the outer layer and bubblewrap may miss it, since deny lists are built from paths that exist at launch.
  - **Spike result (2026-09-28, srt 1.0.0, WSL2 kernel 6.18): not shipped.** Plain bubblewrap nests inside `srt`, network namespace included. A full sandbox runtime inside `srt`, which is what Claude Code's Bash sandbox is, doesn't start: its proxy can't open its Unix socket (`listen EPERM`), and still can't with `allowAllUnixSockets` on the outer layer (`listen EACCES`). Making it work would mean weakening the outer layer. So M9 ships the two layers. Listed paths that don't exist yet turned out to be covered after all: srt 1.0 denies them by mounting an empty read-only placeholder, and preflight tests it.
- Tests removed: `test_m2.py::test_maker_waits_for_a_human_on_ask`, because the build never pauses. `test_m1.py::test_rulings_are_logged_and_ask_goes_to_inbox` didn't need to change: `Project.check` still raises an inbox item when called directly, and every agent run now passes `defer_asks`, so "ask" means deny and record wherever an agent is involved.
- **You run** `parallax preflight 3f9a1c`. **You see:**
  ```
  bash layer   0 of 12 protected paths writable     ok
  tool layer   0 of 60 protected writes allowed     ok
  network      blocked, including the ui port       ok
  environment  no keys or tokens visible            ok
  ready to launch.
  ```
  Then `parallax build 3f9a1c` prints `building 3f9a1c, estimated budget $2.00. parallax stop ends it.`

#### M10: the check

- Parallax stages the worktree into a temporary index, records the tree hash, and refuses binaries or symlinks the plan didn't list.
- Plan conformance and the diff cap are checked by code, before the checker runs. Files that run automatically are flagged.
- Parallax runs the plan's tests with `srt`, on a copy of the reviewed tree with the base branch's test-harness files, and records the results.
- The blind checker gets exactly its brief input. REVIEW.md is written here.
- Rework: up to 3 recorded cycles. The maker gets the checker's findings. On re-review the checker gets only its brief input with the new diff, never the maker's reply. The 4th fail becomes Decision needed.
- Tests changed: `test_m2.py::test_checker_is_blind_to_maker_explanation` becomes the exact-input pin test, including on re-review. `test_disagreement_goes_to_inbox_and_needs_a_reason` and `test_checker_error_goes_to_inbox_without_retry` change to match the rework rule.
- Built in M10 as planned, plus `parallax recheck <task>` to check a task that's already built (003876 was built in M9, before the check existed). The command isn't `check`, which M7 cut and its test keeps cut.
- **You run** `parallax show 3f9a1c`. **You see** a lint-clean report (the example first read "Ready. The checker passed", two sentences, which the brief's one-sentence Bottom line and lint refuse):
  ```
  Type: Decision needed
  Bottom line: Ready: the checker passed and 3 of 3 plan tests pass.
  Not looked at: nothing
  Next: you run parallax accept 3f9a1c, or reject it with a reason.
  Found
  - tests/test_readme.py: 3 of 3 passed (ledger 4be1a0c2)
  - checker: pass, no findings (ledger 7d02e9b1)
  ```

#### M11: accept (the core loop is complete; start daily use here)

- `parallax accept` re-checks every approved file's hash and runs the secrets scan. It writes `record.md` from the ledger.
- It commits exactly the reviewed tree plus `docs/tasks/<id>/` using git plumbing (`commit-tree`, which runs no hooks), with trailers. The commit is signed if you set up a key. It prints the merge command.
- The next command confirms whether you merged the commit unchanged. `docs/THREAT_MODEL.md` is written here, including what a signed commit does and doesn't prove, and the OWASP Agentic table.
- Test removed: `test_m6.py::test_the_merge_steps_really_merge`, because accept replaces the manual steps; a new test runs accept, then the printed merge.
- **You run** `parallax accept 3f9a1c`, then `git log -1 --format=%B parallax/3f9a1c-readme-install-steps`. **You see:**
  ```
  accepted 3f9a1c as 5d2e9f1. merge it yourself:
  git merge --ff-only parallax/3f9a1c-readme-install-steps
  ```
  followed by the commit message ending in `Parallax-Task: 3f9a1c`, `Intent:`, `Approved-By:`, `Verified-By:`, `Ledger-Head:`.

#### M12: the UI for the lifecycle

- Inbox items become gates, Decision needed, and Ready, in the output shape. Accept and merge are marked human-only, with the command to run.
- Approving in the page uses the approval key through the server, which runs as you outside the sandbox.
- Tests confirm the token, the Host and Origin checks, escaping of agent text, the content security policy, and that sandboxed Bash can't reach the port.
- **You run** `parallax ui` in WSL, then open the printed link in your Windows browser. **You see** task 3f9a1c at Ready, with an accept instruction marked "only you can do this".

#### M13: evals in SWE-bench format, and stats

- The 11 cases are converted to the instance format. About 10 new cases are added for known failure types, including behavior existing tests miss.
- Adds the test-writer, in the eval only. The report shows catches, misses and false alarms with and without it, and with the maker's model versus the other checker model.
- `parallax eval run` stays manual. `parallax stats` shows the first-pass check rate, rework cycles per task, how long decisions wait on you, and whether the eval results are older than the last change to CLAUDE.md, the policy, REVIEW.md or the prompts.
- Tests changed in `test_m5.py`: the case format and `test_the_eval_policy_allows_only_the_test_command`, which moves to the sandbox.
- **You run** `parallax eval check`, then `parallax stats`. **You see** `21 of 21 cases are sound.`, then:
  ```
  tasks          6 accepted, 1 stopped
  first pass     4 of 6 passed the check first time
  rework         0.5 cycles per task
  waiting on you median 12 min, longest 3 h
  eval results   current
  ```

#### M14: earned autonomy (after about 10 accepted tasks)

- Classes are defined by path patterns that exclude protected paths, symlinks and mode changes. They're proposed from clean-merge evidence and approved by you.
- A matching small task's plan is auto-approved, under the class's default estimated cap. The diff is checked against the class at Ready; if it leaves the class, it becomes Decision needed.
- Laws can return here later on the same mechanism, using the rejection reasons kept in the ledger.
- **You run** `parallax autonomy`. **You see** each class, its clean-run count, and whether it's proposed, approved, or not yet earned.
