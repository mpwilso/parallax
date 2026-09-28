# Parallax

**Agents do the work. You make the calls.**

Parallax is a small orchestration layer for AI coding agents built around one idea: the scarce resource isn't execution, it's judgment. Agents can already write a lot of code. What gets lost is who decided what, on what evidence, and whether anyone actually checked.

## The problem

Most agent tooling is optimized for throughput: more agents, more tasks, more diffs. That works until something ships that nobody really decided to ship. The other extreme asks the human about everything, which turns review into a rubber stamp.

Parallax aims for the middle. The line between "the agent can just do this" and "a human decides" is explicit, written down, and moves only on evidence.

## Core ideas

- **Deny by default.** Every action type starts denied or needs approval. Nothing is allowed because nobody thought to forbid it.
- **Autonomy is earned by evidence.** Parallax records how each action type is ruled on over time. When the record supports it, it proposes a promotion. A human approves the promotion itself, and that decision is logged.
- **Two vantage points.** A maker does the work. A checker reviews it without ever seeing the maker's explanation, only the task and the diff. If they disagree, it goes to a human. Disagreements are never auto-resolved.
- **Honest empty states.** "No finding" is a real answer, not a failure to fill the page.
- **Everything is on the record.** Grants, refusals, verdicts, disagreements, and human decisions go into an append-only ledger, with reasons.

## How it works

1. You give Parallax a goal.
2. The conductor splits it into tasks, each in its own isolated git worktree.
3. A maker agent works each task within the policy.
4. Any action the policy doesn't allow becomes a pending decision in your inbox.
5. A blind checker reviews the diff.
6. Agreement moves the task forward. Disagreement goes to your inbox.
7. Merging is always a human decision.
8. Every step is logged.

## Status

Early. v1 is the engine plus a command-line decision inbox. A visual inbox comes later.

## Quick start (Milestone 1)

```bash
pip install -e .
cd /path/to/some/git/repo
parallax init                      # writes parallax.policy.toml and .parallax/
parallax task new "fix typo in docs"
parallax check <task-id> shell.run # ask the policy about an action
parallax inbox                     # pending decisions
parallax approve <decision-id> --reason "looked at it, fine"
parallax log                       # the ledger
parallax verify                    # confirm the ledger hasn't been edited
```

## Maker and blind checker (Milestone 2)

```bash
pip install -e .[claude]           # the Claude adapter; the core stays stdlib only
parallax run <task-id>             # maker works the task, then the blind checker reviews the diff
parallax run <task-id> --plan      # maker plans first (read-only), a checker reviews the plan
parallax review <task-id>          # run the checker alone on the current diff
parallax inbox                     # permission requests and maker/checker disagreements
parallax approve <id> --reason ".." # on a disagreement: side with the maker
parallax reject <id> --reason ".."  # on a disagreement: side with the checker
```

While the maker runs, any `ask` action pauses it until you approve or reject from another terminal. The checker sees only the task goal and the diff: never the maker's summary, its plan, or commit messages. `pass` and `no_finding` count as agreement and the task becomes `ready`. Anything else goes to your inbox. Agents can't write `parallax.policy.toml`, `mission.md`, or `.parallax/`, whatever the policy says.

## Roadmap

- [x] M1: policy file, ledger, single task in an isolated worktree
- [x] M2: maker and blind checker, plan review, verdicts, disagreement routing
- [ ] M3: conductor on a pulse, driven by a human-owned mission file, several tasks in parallel
- [ ] M4: evidence moves the line both ways: promotion proposals and law proposals, each approved by a human
- [ ] M5: evals against real open-source changes, results published
- [ ] M6: local visual decision inbox

## Evals

Parallax will be tested against already-merged open-source pull requests: take the issue, let Parallax attempt it, compare against what humans actually shipped. Results get published, including the failures.

## Non-goals

- Replacing human review
- Running unsupervised against production
- Being a general agent framework or an IDE
