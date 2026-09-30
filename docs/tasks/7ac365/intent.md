Bottom line: Make the UI tester write and run flows only for what the browser can show, and report OS notifications as untestable instead of failing them, because Field's real OS notifications can't be driven or seen in a test browser.
Not looked at: I did not run the tester against Field or any real notification outcome. I read `parallax/uitest.py`, `parallax/lifecycle.py` and `parallax/lint.py` only by grep, and did not trace the full run path in `parallax/uitest.py`. Code for this already seems present, so the real gap may be smaller than the Problem says.

kind: bug
size: small
title: keeping UI flows to what the browser can show
scope: parallax/uitest.py, parallax/lint.py, parallax/lifecycle.py, tests/test_ui_tester.py

## Problem

Field's outcomes include real OS notifications. A test browser can't drive or see those, so a flow written for one can only fail or be faked. The human will resubmit Field's task once the tester keeps flows to what the browser can show.

The code already has parts of this. `parallax/lint.py:407` matches the "(not browser-testable)" marker on an outcome. `parallax/uitest.py:141` drops marked outcomes from the plan's `user_flows`. `parallax/uitest.py:148` builds the "can't test in a browser" note. `parallax/uitest.py:62` tells the tester not to write flows for out-of-page behavior. `parallax/lifecycle.py:96` tells the plan never to list a marked outcome. `tests/test_ui_tester.py:402` covers a marked notification outcome.

The gap to close is any route by which a flow for a notification, permission prompt or file dialog still gets written or run. That includes an outcome Focus did not mark, or a mixed outcome where only part of it happens outside the page.

## Outcome

1. asked: The UI tester writes and runs flows only for behavior the page itself shows.
2. asked: An outcome about a real OS notification, such as a desktop notification on completion, gets no flow and no test, and cannot fail the task.
3. inferred: Each skipped part is named in Not looked at as "can't test in a browser: <what>".
4. inferred: For an outcome with a page part and an outside part, only the page part gets a flow. The outside part is named as untestable.
5. inferred: If every outcome is untestable, the tester does not start.
6. inferred: Tests in `tests/test_ui_tester.py` cover a marked outcome, an unmarked notification outcome and a mixed outcome. The full test suite passes.

## Constraints

- Keep the "(not browser-testable)" marker and its matching rules in `parallax/lint.py` compatible. Existing marked intents must still work.
- Do not change how flows run for outcomes the browser can show.
- Do not change the required shape of intent or plan files beyond what the outcomes above need.
- Do not add a dependency or a network call to the test suite.
