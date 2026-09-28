# How Parallax works

Agents do the work. You make the calls, at a few points that matter.

## The lifecycle

Parallax follows the AI-native SDLC playbook (https://claude.com/blog/the-ai-native-sdlc-playbook): each stage writes a file the next stage reads, humans own the judgment gates, and agents verify their own work before a human sees it.

1. **Intent.** `parallax intent new "<rough sentences>"` creates the task, its branch and worktree. A read-only drafter writes `docs/tasks/<id>/intent.md`: problem, outcome, constraints, kind, size.
2. **Spec** (large tasks only) and **plan**, drafted the same way. The plan ends with a `toml` block code reads: files, tests, expected size, domains, outside reads, binaries, symlinks, dependencies, review tightening, estimated cost and budget cap.
3. **Gates.** A small task has two touches: approve intent and plan together, then merge. A large task has three: approve intent; approve spec and plan together; merge. Each approval records every file's hash and is signed with a key only you can read.
4. **Build.** `parallax build <task>` runs preflight, then the maker builds the approved plan in Claude Code's sandbox, in the background. `parallax stop` ends it.
5. **Check.** In the same background process: code checks the diff against the plan, Parallax runs the plan's tests in the sandbox with the base branch's test harness, and the blind checker reviews. Failures go back to the maker, up to 3 recorded cycles, then to you. `parallax show <task>` says where it stands.
6. **Accept** (M11) follows.

What we follow from the playbook: files as the hand-off between stages, human gates on intent and plan, agents verifying before review, one record per change. What we skip: the maintain stage and autonomous triggers. Nothing starts without you.

Drafters get no shell, only Read, Glob and Grep inside the task's worktree, and each call is capped by `[budget] drafting_usd` in the policy file. Only Parallax writes `docs/tasks/`.

## The output shape

Everything Parallax writes for you to read and act on uses one shape. `parallax lint <file>` checks it.

- **Header**, always first: **Type** (Decision needed, Recommendation, or FYI, set by code from the task's state), **Bottom line** (one sentence), **Not looked at** (never empty; "nothing" if so), **Next** (who does what).
- **Then only the sections that apply, in this order:** Decisions (`Decide: <question>. Recommend: <option>. Blocks: <what, or nothing>.`), Changed since last time (on a revisit), Found (every item cites `file:line` or a ledger id, or says Unverified), Recommended (only without Decisions), Details (last, skippable).
- **Caps:** header under 40 words, body under 150 words, Details excluded. No em dashes.
- **Lifecycle files** (intent, spec, plan) store only Bottom line and Not looked at at the top. Type and Next are shown from the task's state, so an approved file never changes and its hash stays valid. No word caps.

A lint failure blocks the output: Parallax won't print a report that fails, and won't approve a gate whose files fail.

## Principles

Each line says how it's enforced today: a test, a rule in code, or judgment only.

- **One cost confirmation per task, at plan approval, with a hard cap.** The cap is shown when you approve, and the build gets what's left of it (test: `test_a_task_stops_at_its_cap`). Drafting calls have their own ceiling (test: `test_drafting_cap_comes_from_the_policy`).
- **Unknown boundary actions are refused and recorded.** A build allows routine work and refuses anything the plan doesn't list (test: `test_a_build_allows_routine_work_and_refuses_the_boundary`); "ask" is refused and recorded, never waited on (test: `test_ask_means_refuse_and_record_without_an_inbox_item`). Drafters can't read outside the worktree (test: `test_drafters_read_only_inside_the_worktree`).
- **Reserved rails (merge, secrets, deploy) fail closed and are checked twice, independently.** Merge can't be set in policy (test: `test_merge_cannot_be_delegated`). The sandbox is the second check: no writes to the shared `.git`, no reads of your home folder or the approval key, no network outside the plan, and it refuses to run if unavailable. Preflight tests both layers before every launch (tests: `test_preflight_refuses_anything_that_gets_through`, `test_preflight_against_the_real_sandbox`). The builder starts with a scrubbed environment (test: `test_the_builder_gets_a_scrubbed_environment`).
- **Anything that watches for new work starts in dry-run; going live is a recorded decision.** Nothing watches for work today. Judgment only.
- **One task, one record: retries fold into the task; proposals carry their provenance.** Every draft, failure and gate decision is a ledger entry on its task. The record file comes in M11.
- **The checker is blind, and the brief is pinned.** It gets exactly the outcome, the constraints, REVIEW.md and the diff, on every review (test: `test_checker_is_blind_to_maker_explanation`). Only REVIEW.md's blocking severities block (test: `test_only_review_md_blocking_severities_block`).
- **The maker's own report of passing tests doesn't count.** Parallax runs them, with the harness from the base branch (test: `test_the_test_harness_comes_from_the_base_branch`).
- **One clear next action per inbox item.** Every report has one Next line (rule in lint).
- **Some steps are human-only on purpose (merge), and the UI says so.** Parallax never merges. The UI marks it in M12.
- **Outside input (issue text, repo content, web pages) is data, never instructions.** Drafters are told so, and can't act beyond reading. Judgment only, backed by their read-only tools.
- **Verify before asserting; say when something doesn't make sense.** Found items must cite a real source or say Unverified (rule in lint).
- **A correction made twice goes into CLAUDE.md or REVIEW.md, as a change the human applies.** Judgment only. Rejection reasons stay in the ledger for later.
- **Approved files, verifier tests, and generated files can't be changed by the maker.** Approvals are signed over each file's hash; a changed file blocks the next gate (test: `test_an_approved_file_that_changed_blocks_the_next_gate`). The maker is kept out of `docs/tasks/` and every protected path in both layers, and a protected file in the final diff sends the task to you (test: `test_the_builder_reports_what_went_wrong`).
- **Nothing the maker writes runs as you, or gets merged, without review.** The maker's commands run in the sandbox, the blind checker reviews, and you merge. Accept (M11) adds the rest. The `[build] setup` command runs as you, so it only runs on a clean worktree at its base commit (test: `test_the_setup_command_makes_the_venv_once_as_you`).
