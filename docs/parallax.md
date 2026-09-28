# How Parallax works

Agents do the work. You make the calls, at a few points that matter.

## The lifecycle

Parallax follows the AI-native SDLC playbook (https://claude.com/blog/the-ai-native-sdlc-playbook): each stage writes a file the next stage reads, humans own the judgment gates, and agents verify their own work before a human sees it.

1. **Intent.** `parallax intent new "<rough sentences>"` creates the task, its branch and worktree. A read-only drafter writes `docs/tasks/<id>/intent.md`: problem, outcome, constraints, kind, size.
2. **Spec** (large tasks only) and **plan**, drafted the same way. The plan ends with a `toml` block code reads: files, tests, expected size, domains, outside reads, binaries, symlinks, dependencies, review tightening, estimated cost and budget cap.
3. **Gates.** A small task has two touches: approve intent and plan together, then merge. A large task has three: approve intent; approve spec and plan together; merge. Each approval records every file's hash and is signed with a key only you can read.
4. **Build** (M9), **check** (M10), and **accept** (M11) follow the approved plan.

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

- **One cost confirmation per task, at plan approval, with a hard cap.** The cap is shown when you approve. Enforced from M9 (the build's budget); drafting calls are capped now (test: `test_drafting_cap_comes_from_the_policy`).
- **Unknown boundary actions are refused and recorded.** Rule in code: anything unlisted is denied (test: `test_unlisted_actions_are_denied`). Drafters can't read outside the worktree (test: `test_drafters_read_only_inside_the_worktree`).
- **Reserved rails (merge, secrets, deploy) fail closed and are checked twice, independently.** Merge can't be set in policy (test: `test_merge_cannot_be_delegated`). The second, OS-level check comes with the sandbox in M9.
- **Anything that watches for new work starts in dry-run; going live is a recorded decision.** Nothing watches for work today. Judgment only.
- **One task, one record: retries fold into the task; proposals carry their provenance.** Every draft, failure and gate decision is a ledger entry on its task. The record file comes in M11.
- **One clear next action per inbox item.** Every report has one Next line (rule in lint).
- **Some steps are human-only on purpose (merge), and the UI says so.** Parallax never merges. The UI marks it in M12.
- **Outside input (issue text, repo content, web pages) is data, never instructions.** Drafters are told so, and can't act beyond reading. Judgment only, backed by their read-only tools.
- **Verify before asserting; say when something doesn't make sense.** Found items must cite a real source or say Unverified (rule in lint).
- **A correction made twice goes into CLAUDE.md or REVIEW.md, as a change the human applies.** Judgment only. Rejection reasons stay in the ledger for later.
- **Approved files, verifier tests, and generated files can't be changed by the maker.** Approvals are signed over each file's hash; a changed file blocks the next gate (test: `test_an_approved_file_that_changed_blocks_the_next_gate`). The maker is kept out of `docs/tasks/` in both layers from M9.
- **Nothing the maker writes runs as you, or gets merged, without review.** The blind checker and your merge. Hardened by the sandbox (M9) and accept (M11).
