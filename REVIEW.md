# Review

How Second Eye, the blind checker, reviews a change. You own this file; agents can't write it.

## Passes

1. Does the change achieve the outcome, within the constraints?
2. Correctness: logic errors, edge cases, error handling, off-by-one, wrong defaults.
3. Tests: do they test the new behavior, and would they fail without the change?
4. Safety: secrets, injection, unsafe file or shell handling, files that run automatically.
5. Scope: anything the outcome didn't ask for.
6. Parallax's own rules: the invariants in CLAUDE.md hold, core stays stdlib, new behavior has a test, and no em dash is added in any changed file (a blocker; Parallax also checks for it by code).

## Severities

- blocker: wrong, unsafe, or breaks something that worked.
- major: likely wrong in a case that matters, or a missing test for new behavior.
- minor: works, but should be better.
- nit: style or wording.

Blocking: blocker, major
