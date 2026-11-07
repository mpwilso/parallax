# Real runs of the light conductor

The record of the M15 confirm run: one `parallax do` with two unrelated fixes, on this repo, with real agents (Focus on `claude-sonnet-5-5`), on 2026-11-07, before M15 was merged. The run was capped so the two parts together couldn't pass $6, and it was run once; it isn't rerun for a better result.

Costs are Claude Code's estimates at API list prices, from the ledger. Times are the ledger's, in UTC.

## The setup

The repo's own `parallax.policy.toml`, with four lines changed for this run and put back afterwards: `[budget] small_cap_usd = 2.90` (each part's cap at most $2.90, so two can't pass $5.80), `drafting_usd = 0.50` (each drafting call), `[launch] auto_launch_usd = 6.00` (so two capped parts fit under it together), and a `[conductor]` table with the defaults, `max_parts = 4` and `max_parallel = 2`. `parallax doctor` passed. The code was the M15 branch's, on base 9915cae.

The request, as typed:

> two small doc fixes, unrelated to each other. docs/macos.md calls the liveness check build.is_alive, but the function in parallax/build.py is build._alive, so fix the name there. And docs/brand/README.md still says the lockup is set like the other five tools' lockups so the six read as one set, which the 0.3.1 changelog corrected: only Polarizer, ISR and Mordecai set theirs that way, Loupe's and Max Q's are drawn differently. make that paragraph say so.

## The run

| Part | Task | Title (from its intent) | Scope | Cap | Spent | Ended |
|---|---|---|---|---|---|---|
| 1 | f291a5 | fixing the liveness check name in the macOS doc | docs/macos.md, tests/test_docs.py, tests/test_staledocs.py | $2.90 | $0.13 (split $0.06, intent $0.03, plan $0.04) | needs you: trust the policy's commands |
| 2 | 64a75a | correcting the lockup paragraph in the brand README | docs/brand/README.md, tests/test_brand.py | $2.90 | $0.10 (intent $0.05, plan $0.06) | needs you: trust the policy's commands |

**$0.23 in all, 34 seconds from intake to both launched, 0 touches so far.** Neither part reached Ready: both stopped at the same question before Maker started, and wait in the owner's inbox.

- **18:08:15** `parallax do` made task f291a5, the request's lead, and started its pilot.
- **18:08:24** Focus's split, 9 seconds and $0.06: "Two unrelated one-line doc fixes in different files, so two parts that can run side by side." Code checked it and found nothing wrong; the scopes don't overlap, so neither part waits on the other (ledger 701b8d78).
- **18:08:25** Part 2 became task 64a75a (its `task.created` carries the request, f291a5, and part 2), and its pilot started. From here the two parts drafted side by side: the board showed both as Drafting at 18:08:26 and 18:08:36, each with its own line.
- **18:08:41** Part 1's plan fit, code raised its cap to the $2.90 limit (the repo's past small tasks call for more), and it waited for part 2: "Its plan fits, and it launches once the request's other parts have their plans."
- **18:08:49** Part 2's plan fit. Together the caps were $5.80, within the $6.00 `auto_launch_usd`, so code launched both in the same second, each approval signed and naming the rule: "launch rule: a small task, cap $2.90 within auto_launch_usd $6.00, nothing in review_paths; this request's 2 parts together cap $5.80, within auto_launch_usd $6.00" (ledger f57e3438, 1bcc93ad). No question was asked about money.
- **18:08:49** Both builds stopped before the setup ran: "the [build] setup command runs as you, and you haven't approved this policy's commands yet". This repo's ledger has no trust recorded for its current `[build] setup` and `[merge] test_command`, so the first task to need them asks. That is the owner's call (whose call: security), so it was left for the owner. `parallax inbox` then showed:

```
request f291a5, 2 parts: two small doc fixes, unrelated to each other. docs/macos.md calls the liveness check bu...
  f291a5  needs you fixing the liveness check name in the macOS doc
  64a75a  needs you correcting the lockup paragraph in the brand README
parallax show <task> for its card.
```

`parallax stats` added the request's line: 2 parts, 0 touches, $0.23.

## What it confirmed, and what it didn't

Confirmed with real agents: Focus split a two-fix request into two parts with disjoint scopes, each part became its own task linked to the request, both drafted at once, the later plan launched both together under the launch rule with the total named, and `parallax inbox` grouped the parts under their request, each with its own state.

Not confirmed: both building at once, and two Ready cards each accepted on its own. Both parts stopped at the trust question before Maker built anything. The suite covers those steps with fakes (`tests/test_conductor.py`); this run didn't reach them.

## Misses

- **One question, asked twice.** The trust question is about the repo's policy, and both parts asked it, so the owner has two cards for one decision. Trusting on one card records it for the repo, but the other card keeps its own open item and needs its own answer. It isn't new in M15 (two tasks of their own would each ask too), but a split makes it likely. Proposed: when you trust a policy's commands, every open trust item with the same commands is answered with it.
- **The run started without the repo's commands trusted.** Checking for a `policy.trusted` entry before the run would have shown the stop was coming. The run was not repeated.

## Left for the owner

Both tasks wait in the owner's inbox: `parallax decide f291a5 trust` (or Trust on the card) runs the policy's commands and the build goes on; the second card asks the same. Nothing was accepted or merged.
