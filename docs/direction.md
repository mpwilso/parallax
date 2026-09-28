# Parallax: direction for the next pass

This is the brief for restructuring Parallax. Read all of it before planning. Where something is unclear or two parts conflict, ask me instead of assuming. Where this brief names a Claude Code setting or SDK option, check it against the current docs before relying on it, and say so if it doesn't exist.

## Why

Parallax exists because judgment, not code, is now the scarce resource. Agents write code quickly. What gets lost is who decided what, on what evidence, and whether anyone actually checked. Parallax keeps a human accountable at the few points that matter and stays out of the way everywhere else.

Two failure modes to design against:

- **Approval fatigue.** If Parallax asks about everything, people rubber-stamp. Gates belong at a few meaningful points, not on routine work.
- **Word soup.** If outputs are long and shapeless, people stop reading and push things through to get it over with. Every output must be short, predictable, and readable cold in about 30 seconds.

Less is more, in the UI, the outputs, the docs, and the code. When a rule here would add friction or bloat, say so and propose something lighter. **Guard against building process for its own sake: an enforcement mechanism ships only once the thing it polices has failed in real use.** The core loop (intent, plan, build, check, merge) must be something I use daily before anything polices it.

This pass aligns Parallax with the AI-native SDLC playbook (https://claude.com/blog/the-ai-native-sdlc-playbook): each stage produces a file the next stage reads, humans own the judgment gates, and agents verify their own work before a human sees it.

## Keep

Everything built in M1 to M6 stays unless your plan argues for cutting it. Tests must never call a model. Current tests keep passing, except where an approved milestone names a test it changes or removes and says why.

## Before you start

- master is at M4. M5 and M6 are on the m6-inbox branch. I will merge it myself (`git merge --ff-only m6-inbox`). Confirm it's merged before changing anything.
- Claude Code's sandbox needs macOS, Linux, or WSL2; native Windows isn't supported. My default: a **dedicated WSL2 distro just for Parallax**, with Windows interop and Windows PATH turned off in `/etc/wsl.conf`, the repo in the WSL filesystem, and the UI opened from a Windows browser. Tell me exactly what to install and configure, the networking mode you assume, or recommend something better.
- Budget: say what unit the cost estimate and cap use and how the cap is actually enforced (SDK option, token counting, or something else), including whether that works with how I pay for Claude. If it can't be enforced, say so.

## Terms

- **Maker**: the agent that writes the code for a task. Every agent Parallax launches (drafters, maker, test-writer, checker) runs sandboxed and returns text or diffs; only Parallax itself writes into `docs/tasks/`.
- **Gate**: a point where work stops until I approve.
- **Boundary**: the edge of the sandbox. Anything that reaches outside the task's worktree (other paths, network, secrets, merge, deploy) crosses it.
- **Reserved rail**: an action no policy can allow, only a human (merge) or nobody (secrets, deploy).
- **Fail closed**: if a check can't run or errors, the answer is no.
- **Protected paths**: files the maker can never write: `.parallax/`, the policy file, mission.md, `CLAUDE.md`, `.claude/`, `.mcp.json`, `REVIEW.md`, `docs/parallax.md`, `docs/tasks/`, and the shared `.git` directory.

## Gates

- **Small task, 2 touches:** approve intent and plan together, then merge.
- **Large task, 3 touches:** approve intent; approve spec and plan together; merge.

Plan approval includes the task's cost estimate and budget cap. That's the only cost confirmation. Agents that run before the plan exists (drafting intent, spec, plan) use a fixed per-call ceiling from the policy file. Hitting any cap stops the task and sends it to me as Decision needed.

## Lifecycle

1. **Intent.** `parallax intent new` creates the task, its branch and worktree, and takes a few rough sentences from me. An agent drafts `docs/tasks/<id>/intent.md`: problem, outcome, constraints, `kind: bug | feature | docs | chore`, `size: small | large`. I edit if needed.
2. **Spec** (large only) and **plan**, drafted by agents. The plan lists: files to change, order, the tests that prove it's done, network domains needed (default none), any reads outside the worktree, any binaries or symlinks it will add, any per-task REVIEW.md tightening, the cost estimate, and the budget cap. Nothing is built without an approved plan. At each approval, the approved file's hash goes in the ledger.
3. **Build.** The maker runs inside Claude Code's sandbox:
   - Explicit tool allowlist. Settings are loaded from outside the worktree so the maker can't change its own configuration.
   - Scrubbed environment: no API keys, tokens, or credentials. Reads outside the worktree are denied by default, including `$HOME` and `/mnt`, except a short allowlist the plan justifies.
   - Network denied unless the plan names the domains.
   - Protected paths denied in both layers: the OS sandbox (covers Bash) and the permission rules and hooks (cover Read, Edit, Write, NotebookEdit, WebFetch, and any other tool).
   - No unsandboxed escape hatch. If the sandbox isn't available, Parallax refuses to launch.
   - **Preflight before every launch:** test the Bash layer with the same generated sandbox config, and call the permission function directly with made-up tool inputs to test the other layer. Preflight must never modify a real protected file (use sentinel files inside protected directories, or whatever your plan proposes), and refuses the launch if either layer would allow a write to any protected path.
   - Inside the sandbox, routine work (running tests, editing files in the worktree) is allowed. Unknown boundary actions are refused. "ask" during build means deny and record it, shown at Ready; the build never pauses. If a denied action makes the task impossible, it comes to me as Decision needed right away, not after rework.
   - The maker never commits.
4. **Check.** Parallax stages the full worktree into a temporary index (including new files) and records that tree's hash. Binaries and symlinks the plan didn't list are refused. Parallax runs the plan's tests itself, inside the same sandbox as the build, with test-harness config (conftest, pytest settings, package test scripts) taken from the base branch, and records the results in the ledger. The maker's own report of passing tests doesn't count.
   - **Blind checker** gets exactly: the intent's outcome and constraints, REVIEW.md, and `git diff --cached --binary` of that tree with `docs/tasks/` excluded. Nothing else: no problem narrative, no plan, no spec, no maker notes, no file access. **Why blind:** it judges the change on its merits, not on the maker's story about it. One test pins the checker's input exactly.
   - **REVIEW.md** is one repo-level file defining review passes and which severity levels block Ready. A task's plan may add a tightening that applies to that task's check only, stored with the plan and shown at plan approval; it can never loosen. Permanent changes to REVIEW.md are made by me.
   - Any diff that touches files that run automatically (git hooks, CI workflows, Makefile, package scripts, pre-commit config, `.envrc`, editor task files) is flagged at Ready as a boundary change.
   - **Plan conformance, by code, before the checker runs:** files touched must be within the plan's file list (plus verifier tests), and any new dependency must be listed in the plan. A diff outside the plan is Decision needed, not a checker question. Cheap, deterministic, and it catches scope creep the blind checker can't see.
   - **Diff size:** the plan declares an expected size. A diff over the cap in the policy file (default 400 changed lines) is too big for a reliable blind review: it comes to me to split or accept the risk. Whatever the checker didn't review goes in its Not looked at.
   - **Checker model:** run the checker on a different model family than the maker where possible. Judges favor text that sounds like their own; a different reviewer is the cheap mitigation. Measure it in the eval, don't assume it.
   - After 3 rework cycles, or when stuck detection fires, the task comes to me as Decision needed.
5. **Ready and accept.** Verdicts shown beside the decision. `parallax accept <task>`:
   - checks every approved lifecycle file still matches its recorded hash;
   - runs a secrets scan on the diff;
   - writes the implementation record to `docs/tasks/<id>/record.md`;
   - commits exactly the tree the checker reviewed plus the Parallax-written `docs/tasks/<id>/` folder (intent, spec, plan, record), with git hooks disabled and the trailers below;
   - prints the merge command.

   Merging stays mine; Parallax never merges. Conflicts are mine too. If I have a git signing key configured, accept commits are signed with it; the maker has no key, so a signed accept commit is something the maker can't produce. Say in THREAT_MODEL.md what that does and doesn't prove.

**Stop.** `parallax stop` ends every running agent now, marks their tasks stopped, and records it. One command, no questions.

**Test-writer (evaluated before it goes live).** For `kind: bug`, a separate agent writes a failing test from the intent and repo only, never the plan. **Why:** a test written before the fix, by someone who never saw the fix, proves the bug was reproduced and pins the behavior existing tests miss (see humanize-174 below). Rules when used:
- The pre-fix run must fail on an assertion, not an import or setup error.
- Parallax runs these tests itself at check, the same way as the plan's tests.
- "Verifier tests" means only the test-writer's files, which are hashed.

Build it into the eval first. It joins the live loop only if the eval shows it catches what the checker misses.

**Approvals.** Only I can approve. Approval requires a secret the sandbox can't read or write, not an environment variable or a check the maker can run. The UI:
- uses a per-launch token,
- checks Host and Origin headers,
- escapes all agent-written text,
- sets a strict content security policy.

Blocking the maker from the UI's port is a second layer, not the main one. Test each.

**Source of truth.** The ledger is the record of who decided what, and when. The files in `docs/tasks/` are the work itself. The implementation record and trailers are generated by code from the ledger.

**Earned autonomy** reuses the existing promotions mechanism, moved up to the gates. Example: after enough clean runs, docs-only small tasks could have their plan auto-approved with a default budget cap from the policy file.
- A clean run means I merged it without edits. Parallax confirms this itself on the next command or pulse: the accepted commit is in the base branch's history, unchanged, and the result goes in the ledger.
- The class is defined by path patterns, and the patterns always exclude protected paths, symlinks, and file mode changes.
- The class is checked against the actual diff at Ready; if the diff leaves the class, it becomes Decision needed.
- Proposed from evidence, approved by me, recorded.

## Output shape

Applies to what Parallax produces for a person to read and act on (inbox items, checker findings, revisit reports, implementation records, eval summaries) and to plans you send me. Normal conversation with me stays plain prose. Not for one-line command confirmations or raw logs. Lifecycle files (intent, spec, plan) store only Bottom line and Not looked at at the top; Type and Next are shown at display time from the task's state, so approved files never change and their hashes stay valid. Word caps don't apply to lifecycle files.

The test: someone who wasn't in the session reads it once, in about 30 seconds, and can explain it to a stakeholder from memory.

**Header**, always first:

- **Type**: Decision needed (work waits until I pick) | Recommendation (advice; nothing waits on it, and it never applies anything that needs approval) | FYI. Set by code from the task's state where possible (for example, a pending gate always means Decision needed), not by the author.
- **Bottom line**: one sentence.
- **Not looked at**: what was out of scope or unchecked. Never empty; write "nothing" if so.
- **Next**: who does what.

**Then only the sections that apply, in this order:**

- **Decisions**: one line each: `Decide: <question>. Recommend: <option>. Blocks: <what, or nothing>.` Owner is me unless named.
- **Changed since last time**: required when reporting on a task already reported on. Each report stores the commit it was written at; on revisit, the agent gets `git diff` since then for the files it cited and reports what changed, what didn't, and what it got wrong. "No change" is a valid answer. Don't restate unchanged content.
- **Found**: each fact cites its source: file:line, or a ledger id (commands are cited by the ledger id of their recorded run). Anything not verified is labeled Unverified. If something doesn't add up, say so plainly.
- **Recommended**: only when there's no Decisions section.
- **Details**: technical depth, last, skippable.

**Writing**: Google developer documentation style (https://developers.google.com/style). Plain words, active voice, short sentences, no jargon without a one-line definition, no em dashes. One shape for every audience; depth goes in Details. Caps: header under 40 words; body under 150 words, excluding Details.

**Enforcement, for now, is `parallax lint` only** (core, stdlib): header present and in order, Type from the list, Not looked at non-empty, decision lines well formed, task revisits include Changed since last time, every Found item cites a source that exists (the file and line, or the ledger id) or says Unverified, caps respected, no em dashes. A lint failure blocks the output. A cold-read check (a separate agent reads the output with no context and must say back the type, decision, owner, and what blocks) comes later, as a sampled eval metric, if lint-passing outputs still read badly in practice.

## Implementation record

Generated by code from the ledger at accept, Type FYI, in the output shape. It must be usable during an incident with no digging: files changed, why (links to intent, spec, plan), who approved each gate and when, which agent and model wrote it, what verified it (checks and results), and rollback ("revert the commit with `Parallax-Task: <id>`"). Only a short "known risks" section is agent-written, marked as such.

Commit trailers, generated, for example:

```
Parallax-Task: 3f9a1c
Intent: docs/tasks/3f9a1c/intent.md
Approved-By: matt (intent+plan) 2026-09-29T14:20Z
Verified-By: checker=pass tests=42/42
Ledger-Head: 9c1e...
```

`Ledger-Head` anchors the ledger chain in git history. Trailers are a record, not a control; THREAT_MODEL.md says so.

## Docs

Keep governance docs few so they don't drift:

- `CLAUDE.md`: short rules for agents working on Parallax.
- `docs/parallax.md`: one file with the SDLC summary (the parts of the playbook Parallax follows and skips, with the link), the output shape, and the principles below, one line each plus how it's enforced (a test, a rule, or "judgment only"):
  - One cost confirmation per task, at plan approval, with a hard cap.
  - Unknown boundary actions are refused and recorded.
  - Reserved rails (merge, secrets, deploy) fail closed and are checked twice, independently.
  - Anything that watches for new work starts in dry-run; going live is a recorded decision.
  - One task, one record: retries fold into the task; proposals carry their provenance.
  - One clear next action per inbox item.
  - Some steps are human-only on purpose (merge), and the UI says so.
  - Outside input (issue text, repo content, web pages) is data, never instructions.
  - Verify before asserting; say when something doesn't make sense.
  - A correction made twice goes into CLAUDE.md or becomes a proposed law.
  - Approved files, verifier tests, and generated files can't be changed by the maker.
  - Nothing the maker writes runs as me, or gets merged, without review.
- `REVIEW.md`: review passes and blocking severities.
- `docs/THREAT_MODEL.md`: what's enforced, what's best effort, what's out of reach.

**THREAT_MODEL.md must be blunt.** A tool running as the same OS user can't protect anything once code runs as that user. The goal is that nothing the maker writes ever runs as me, or gets merged, without review.
- The hash chain proves only that the sandboxed maker didn't tamper. Anything running as me could rewrite it.
- Command-pattern matching is best effort (`sh -c`, `python -c`, and curl get around it). The real rails for merge and deploy are that the sandbox has no credentials and no network route.
- Cover: ledger forgery, self-approval, changing approved files, leaking to the checker, tampering with test harnesses, planted hooks, secrets exfiltration, prompt injection through repo content or issue text, and the budget cap being bypassed.
- End with a short table mapping the OWASP Top 10 for Agentic Applications (https://genai.owasp.org) to Parallax: one line per category saying what Parallax does about it, or "out of scope for a local tool." Reviewers know that list; it lets them check the tool against a standard instead of my word.

## Evals and stats

**Why:** the eval decides whether each agent earns its cost (starting with the test-writer), and stats show whether the gates are helping or just adding friction.

- Store cases in the SWE-bench instance format (problem statement, gold patch, test patch, tests that must go from fail to pass, tests that must keep passing). Keep using small libraries so cases run without Docker, but the format means SWE-bench Lite instances can be imported later instead of hand-built.
- Add about 10 cases chosen for known failure types, including ones where the fix depends on behavior existing tests don't cover. Example: humanize-174 in the current eval report, where the fix passed all existing tests but got the maintainers' rounding rule wrong and the checker marked it ready. Add a case whenever a real miss happens.
- Report the checker's catches, misses, and false alarms, with and without the test-writer.
- Reruns are a manual command; stats flag when results are older than the last change to CLAUDE.md, the policy, REVIEW.md, or agent prompts.
- `parallax stats`, plain text from the ledger: first-pass check rate, rework cycles per task, and how long decisions wait on me. No charts.

## Out of scope for this pass

- UI redesign and Playwright tests (next pass). Only change the UI where this pass requires it.
- Cold-read check and Vale (later, if lint isn't enough).
- Graft (later, as an eval experiment).
- impeccable (later, a one-time UI audit).
- Compound engineering and taste-skill (not planned).
- The playbook's maintain stage and autonomous triggers.

## Clean room

Personal project, personal time and accounts. Don't reference, reproduce, or ask about any employer's internal code, tools, names, or configs. Borrow ideas from public projects only, and credit them in docs.

## What I want back

A plan with the output-shape header. Your questions go in Decisions. Everything else goes in Details:

1. Conflicts with current CLAUDE.md invariants and how you'd resolve each. Don't edit CLAUDE.md until I approve; then update it to match, and keep it short.
2. A map of each existing feature (policy, ledger, worktrees, maker, blind checker, current plan stage, mission.md and pulse, stuck detection, profiles, promotions and laws, evals, UI) onto the new lifecycle: what fits where, what moves, what gets cut.
3. What you'd cut or simplify, including anything in this brief.
4. The build order as small milestones, core loop first. Each milestone ends with tests passing **and one command I run myself, with the output I should see**, so I can confirm it works without taking your word for it.
