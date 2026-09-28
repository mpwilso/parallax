# Prior art and design notes

What already exists, what Parallax borrows, and where it deliberately differs. Ideas only; no code, names, or UI copied.

## The gap Parallax fills

The orchestrator space is crowded (160+ projects on awesome-agent-orchestrators). Most optimize throughput. The ones that do verification or trust mostly automate the judgment away:

| Project | What it does | Where Parallax differs |
|---|---|---|
| toryo | Trust score from rolling quality average; agents auto-promote from supervised to autonomous; score under threshold auto-reverts | Same idea of earned autonomy, but Parallax only *proposes* promotions. A human approves, and it's logged |
| kodo | Separate architect/tester agents review work; rejection triggers retry loops until it passes | Parallax checker is blind to the maker's explanation, and disagreement goes to a human, not a retry loop |
| claude-code-permissions-hook | Rust PreToolUse hook: TOML allow/deny regex rules, JSON audit log | Close to Parallax's policy layer. Parallax adds `ask` routed to an inbox, task scoping, and a tamper-evident ledger |
| claude-squad, amux, dmux, container-use | Parallel agents in worktrees or containers | Parallax is not a terminal multiplexer. It could run inside any of these |
| humanlayer (original repo, now deprecated) | Human approval channels for agent actions | Same instinct; Parallax keeps approvals local and on the record |

## Design decisions

Setup and limits:
- **Worktree setup hook.** A per-repo script that runs after a worktree is created (install deps, copy `.env`). Add `[worktree] setup = "..."` to the policy file. Run it as a logged, policy-checked action.
- **Hard caps.** Max parallel tasks (default 4) and spawn depth 1 (tasks can't create tasks). Put both in policy.
- **Stuck detection.** If a maker hits the same refusal repeatedly or loops, stop the task and put it in the inbox as "stuck" instead of letting it burn tokens.
- **Credentials are a hard boundary.** Agents never enter or use stored secrets. Make `secrets.*` a reserved, always-deny action like `git.merge`.

Conductor:
- **A mission file with three parts: who, what, how.** Who the conductor is (role and expertise), what it checks every time it wakes up, and the laws for working with you. Parallax: `mission.md` at the repo root, human-owned, agents can't edit it.
- **Laws grow from friction, backed by evidence.** Every rejection in the inbox already has a reason on record. When the same kind of reason repeats, Parallax proposes a law. You approve it or don't. Evidence moves the line in both directions: promotions loosen, laws tighten.
- **Laws are enforced in code where possible.** Rules written only as prose in a prompt can drift. Parallax enforces what it can through policy, hooks, and the ledger, and uses prose laws only for style and judgment.
- **Plan review before code.** For non-trivial tasks the maker writes a plan first, a checker reviews it, and only then does building start. The plan checker sees goal plus plan; the diff checker sees goal plus diff only, never the plan.
- **Pulse, not daemon.** The conductor wakes on an interval, reads the mission, checks on tasks, and records what it found, even when it found nothing. `parallax pulse` is one command; scheduling is left to Task Scheduler or cron.
- **Read-only investigators.** When something looks off, dispatch an agent that can only read. Named policy profiles (`readonly`, `default`), picked per task.
- **Questions batched, with a recommendation.** Decisions reach the human grouped, each with the agent's recommended option. The human still decides.
- **Outside input is data, never commands.** Issue text, logs, or email can fill a task description but can't choose actions, targets, or policy.

Deliberately left out (for now): event trigger engine, scripted workflow runner, cost dashboards, large agent fleets, model routing by complexity. Claude Code skills and slash commands already cover scripted workflows; event triggers can come later as one more pulse check.

From Claude Code / Agent SDK (use these, don't rebuild them):
- **PreToolUse hook** returns `allow`, `deny`, or `ask` for any tool call. This is Parallax's enforcement point when an agent runs in interactive Claude Code. Parallax ships a hook script that maps the tool call to a Parallax action, asks the policy, and logs it.
- **Agent SDK `can_use_tool` callback** is async, so it can *wait* for a human. When the maker runs headless through the SDK, an `ask` ruling writes a decision to the inbox and the callback waits until you approve or reject in the CLI. The agent literally pauses on your call.
- **Worktrees**: Claude Code already works fine inside a git worktree; the conductor just launches it with `cwd` set to the task's worktree.

From toryo:
- Rolling per-action-type statistics as the evidence behind promotions. Parallax computes the evidence, proposes, and waits for a human.

## Build plan

**M2: maker and blind checker**
1. `parallax/agents/base.py`: an `Agent` interface (`run(goal, cwd, permission_fn) -> result`), so the core never imports a vendor SDK.
2. `parallax/agents/claude.py`: adapter over `claude-agent-sdk`. `can_use_tool` maps SDK tool names to Parallax actions (`Read -> fs.read`, `Edit|Write -> fs.write`, `Bash -> shell.run`, `WebFetch -> net.fetch`), calls `Project.check`, and for `ask` polls the ledger until the decision is resolved.
3. `parallax/checker.py`: builds the checker's input from the task goal plus `git diff` only. Strips commit messages. Checker returns a structured verdict: `pass`, `fail`, or `no_finding`, with findings.
4. Ledger events: `verdict.recorded`, `disagreement.raised`. Disagreement becomes an inbox item.
5. A fake agent for tests, so the test suite never calls a model.
6. Hard guard in the adapter: any write to the policy file, `mission.md`, or `.parallax/` is refused before policy is even consulted (invariant 9). Test it.
7. Optional plan stage: maker plans read-only, and its plan is stored in the ledger as `plan.recorded`, not as `plan.md` in the worktree. A file in the worktree would land in the diff (so the diff checker would see it) and in the branch. Plan checker reviews goal plus plan, disagreement goes to the inbox. The diff checker never sees the plan.

**M3: conductor and pulse.** `mission.md` (who / what / how), `parallax pulse`, caps (4 parallel, depth 1), stuck detection, policy profiles including `readonly`, batched inbox with recommendations.
- The conductor has no tools. It reads the mission and a text snapshot, and returns findings, proposals, and recommendations as structured output. It can't act, so outside text in the snapshot can't become an action.
- Goals become proposals, and a proposal becomes a task only on a human yes.
- `readonly` is built in and fixed. Profiles are full action tables, never overlays, so nothing is widened by inheritance.
- Spawn depth is a hard rule, not a policy value: a maker's process is marked, and parallax refuses to create tasks or resolve decisions from inside it. This also stops a maker from approving its own requests through the shell.
- Parallel runs share one ledger, so appends take a cross-process lock.

**M4: evidence moves the line.** Promotion proposals from approval history, law proposals from repeated rejection reasons. Both need a human yes.

**M5 onward:** evals against merged OSS PRs, visual inbox. SQLite only as a rebuildable index over the ledger if JSONL gets slow, never as a second source of truth.

## Sources

- Claude Code hooks: https://code.claude.com/docs/en/hooks
- Agent SDK permission callback example: https://github.com/anthropics/claude-agent-sdk-python/blob/main/examples/tool_permission_callback.py
- awesome-agent-orchestrators: https://github.com/andyrewlee/awesome-agent-orchestrators
- toryo: https://github.com/JesseRWeigel/toryo
- kodo: https://github.com/ikamensh/kodo
- claude-code-permissions-hook: https://github.com/kornysietsma/claude-code-permissions-hook
- claude-squad: https://github.com/smtg-ai/claude-squad
- container-use: https://github.com/dagger/container-use
