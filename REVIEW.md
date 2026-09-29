# Review

How Second Eye, the blind checker, reviews a change to Parallax. You own this file; agents can't write it.

## Passes

1. Does the change achieve the outcome, within the constraints?
2. Correctness: logic errors, edge cases, error handling, off-by-one, wrong defaults.
3. Tests: do they test the new behavior, and would they fail without the change?
4. Safety: secrets, injection, unsafe file or shell handling, files that run automatically.
5. Scope: anything the outcome didn't ask for.
6. Parallax's own rules: the invariants in CLAUDE.md hold, core stays stdlib, CLI output has no em dashes, and new behavior has a test.

## Severities

- blocker: wrong, unsafe, or breaks something that worked. Any em dash added in a changed file is a blocker; Parallax also checks for it by code.
- major: likely wrong in a case that matters, or a missing test for new behavior.
- minor: works, but should be better.
- nit: style or wording.

Blocking: blocker, major
