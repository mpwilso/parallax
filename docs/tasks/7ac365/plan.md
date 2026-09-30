Bottom line: Close the two routes that still let a flow for a notification, permission prompt or file dialog through: an outcome Focus left unmarked, and a mixed outcome with a page part and an outside part.
Not looked at: I did not run the tester or trace `run_with`, `FakeTester` or the plan lint end to end. I did not read `parallax/lifecycle.py` beyond the marker lines. The marked-outcome path, the skip when every flow is marked, and the "can't test in a browser" note already exist and have tests, so the real gap is only the unmarked and mixed cases.

## Steps
1. `parallax/lint.py`: add `OUTSIDE_PAGE`, a case-insensitive regex for OS-level terms (desktop or OS notification, push notification, permission prompt, file dialog, file picker, system tray). Add `outside_page_terms(text)`, which returns the matched terms. Leave `UNTESTABLE` and `browser_untestable` as they are, so marked intents behave the same.
2. `parallax/uitest.py`, `PROMPT`: add to the existing rule that an outcome can have both a page part and an outside part. Only the page part gets a flow. The outside part is named in `not_looked_at` as "can't test in a browser: <what>". The prompt also says a flow's name and `saw` must describe only what the page shows.
3. `parallax/uitest.py`, the run path near line 406: after the marked-outcome filter, also drop any written flow whose reply entry is for an unmarked outcome and whose `name` or `saw` matches `lint.outside_page_terms`. Do not drop a flow for the page part of a mixed outcome. A mixed outcome's flow names and `saw` come from the page part, so the term check applies to the flow and not to the outcome line. Add `outside_flows(reply, untestable)` as a small helper to keep `run` readable.
4. `parallax/uitest.py`, `untestable_note`: for each dropped unmarked flow, add "outcome N (<the outside part>)" to the note, using the dropped flow's `saw`. A fully marked outcome keeps its current wording. Keep the note in `rec["data"]["not_looked_at"]` so the report shows it.
5. `parallax/uitest.py`, `applies` and `flow_outcomes`: no change for marked outcomes, which already stop the tester from starting when every flow is marked. Add a test for this instead of code.
6. `parallax/lifecycle.py`: extend the intent shape text at line 46 to say that an outcome mixing page and outside behavior should be split, or have only its outside clause named. Do not change the required shape or the marker rule.
7. `tests/test_ui_tester.py`: add the tests below, reusing `untestable_docs`, `TwoFlowTester` and `run_with`.

## Tests
New tests first:
- `test_an_unmarked_notification_outcome_gets_no_flow`: an intent outcome "A desktop notification appears when it's done" with no marker. The fake tester writes a flow for it. Assert the file is not kept, the run is not failed, and Not looked at names it.
- `test_a_mixed_outcome_keeps_only_its_page_flow`: one outcome with a page part and a notification part. The tester writes a page flow and a notification flow. Assert only the page flow is recorded, and the notification part is named as "can't test in a browser".
- `test_outside_page_terms_match_os_behavior_only`: unit test of `lint.outside_page_terms`. It matches notification, permission prompt and file dialog, and does not match an in-page "toast" or "alert".
- `test_a_prompt_tells_the_tester_to_split_mixed_outcomes`: the prompt text contains the new rule.

Existing tests that must still pass: `test_an_outcome_outside_the_page_is_never_a_flow_and_is_named_under_not_looked_at`, `test_a_plan_whose_only_flows_are_outside_the_page_never_starts_field` and `test_focus_marks_outcomes_a_browser_cant_test_and_the_plan_leaves_them_out`. Then run the full suite.

## Risks
- The term regex could drop a real page flow whose name or `saw` mentions a word like "notification" for an in-page banner. Keep the terms to OS-level phrases and test an in-page case.
- The tester may word a mixed flow's `saw` with an OS term and lose the page flow. The prompt rule in step 2 and the check on the flow only, not the outcome, reduce this.
- Changing the intent shape text in `lifecycle.py` could break shape lint tests that compare it. Add words only.
- A dropped flow could leave no flows at all. That path already returns "wrote no tests" to the human, so check the wording stays accurate.

```toml
files = ["parallax/uitest.py", "parallax/lint.py", "parallax/lifecycle.py", "tests/test_ui_tester.py"]
tests = ["tests/test_ui_tester.py"]
lines_changed = 120
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = ""
estimated_cost_usd = 1.00
budget_cap_usd = 3.40
covers = { "1" = ["tests/test_ui_tester.py"], "2" = ["tests/test_ui_tester.py"], "3" = ["tests/test_ui_tester.py"], "4" = ["tests/test_ui_tester.py"], "5" = ["tests/test_ui_tester.py"], "6" = ["tests/test_ui_tester.py"] }
user_flows = []
```
