Bottom line: This task corrects the lockup paragraph in docs/brand/README.md, which wrongly says all six tools set their lockups alike, to match the 0.3.1 changelog.
Not looked at: The full text of tests/test_brand.py beyond a keyword search, the other brand files, and the actual lockup art of Loupe and Max Q (they are in other repos).

kind: docs
size: small
title: correcting the lockup paragraph in the brand README
scope: docs/brand/README.md, tests/test_brand.py

## Problem
The "The lockup" section of docs/brand/README.md says the word PARALLAX is "set like the other five tools' lockups: a system sans at weight 800 with wide tracking, stretched to a fixed length, so the six read as one set" (docs/brand/README.md:17). That is false. CHANGELOG.md:21 and CHANGELOG.md:27 say the 0.3.1 release corrected it: only Polarizer, ISR and Mordecai set theirs that way. Loupe's word is Unbounded SemiBold as outlines, and Max Q's capitals are drawn as paths. The same "other five tools" wording is in the docstring of `test_the_lockup_is_the_animated_mark_with_the_series_wordmark` (tests/test_brand.py:151), which this task may touch if it needs the same fix.

## Outcome
1. asked: The paragraph in the "The lockup" section of docs/brand/README.md no longer says the lockup is set like "the other five tools'" lockups or that "the six read as one set".
2. asked: The paragraph says that only Polarizer, ISR and Mordecai set their lockups this way: a system sans at weight 800 with wide tracking, stretched to a fixed length.
3. asked: The paragraph says that Loupe's and Max Q's lockups are drawn differently, and that the 0.3.1 changelog corrected the earlier claim.
4. asked: The rest of the paragraph stays as it is: the UI header's lowercase monospace word, the `<picture>` choice of the dark and light files, and the note that it is the README's heading.
5. inferred: The docstring in tests/test_brand.py:151 does not repeat the "other five tools" claim, if it needs a wording fix. No test assertion changes.

## Constraints
- Change only the one paragraph in docs/brand/README.md. No other section changes.
- The part 1 change to docs/macos.md is a separate task and is not touched here.
- CHANGELOG.md is not edited. It is already correct.
- The brand tests keep passing, and no assertion in them is weakened.
- Plain wording, no em dashes.
