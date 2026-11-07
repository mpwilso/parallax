Bottom line: This change rewrites the lockup paragraph in docs/brand/README.md so it says only Polarizer, ISR and Mordecai set their lockups alike, and that Loupe and Max Q draw theirs differently, as the 0.3.1 changelog says.
Not looked at: The rest of tests/test_brand.py beyond the lockup test, the other brand files, the comments in parallax/brand.py (out of scope), and the actual lockup art of Loupe and Max Q (other repos).

## Steps
1. In docs/brand/README.md, line 17 only, replace the first sentence. The old text is "The animated P and the word PARALLAX, set like the other five tools' lockups: a system sans at weight 800 with wide tracking, stretched to a fixed length, so the six read as one set." The new text is "The animated P and the word PARALLAX, set the way Polarizer, ISR and Mordecai set theirs: a system sans at weight 800 with wide tracking, stretched to a fixed length. Loupe and Max Q draw theirs differently: Loupe's word is Unbounded SemiBold as outlines, and Max Q's capitals are drawn as paths. An earlier version of this page said all six were set alike; the 0.3.1 changelog corrected that." Keep the rest of the line as it is: the UI header's lowercase monospace word, the `<picture>` choice of the dark and light files, and "It is the README's heading." Use no em dashes.
2. In tests/test_brand.py, line 151, change the docstring of `test_the_lockup_is_the_animated_mark_with_the_series_wordmark`. Replace "as the other five tools set theirs" with "as Polarizer, ISR and Mordecai set theirs". Leave the rest of the docstring and every assertion unchanged.

## Tests
- No new tests. The change is prose and a docstring, and the existing brand tests already cover the lockup SVG.
- Run `tests/test_brand.py` to show it still passes. No assertion is edited or weakened.
- Check by reading that the README paragraph no longer contains "other five" or "read as one set", and that it names Polarizer, ISR, Mordecai, Loupe and Max Q.

## Risks
- A brand test could check the README text by string match. A keyword search found none, but running the test file settles it.
- parallax/brand.py comments at lines 321, 322 and 337 still say "other five". That file is outside this task's scope, so they are left for a separate change.
- The new wording must match CHANGELOG.md lines 21 and 27. Re-read them when building.

```toml
files = ["docs/brand/README.md", "tests/test_brand.py"]
tests = ["tests/test_brand.py"]
lines_changed = 6
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = ""
estimated_cost_usd = 0.30
budget_cap_usd = 2.90
covers = { "1" = ["tests/test_brand.py", "Step 1"], "2" = ["Step 1"], "3" = ["Step 1"], "4" = ["Step 1", "tests/test_brand.py"], "5" = ["Step 2", "tests/test_brand.py"] }
user_flows = []
```
