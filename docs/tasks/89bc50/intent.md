Bottom line: This task makes the page show a browser notification when a task becomes Ready or needs the user, and makes a `#TOKEN&task=TASKID` link open that task's card, so the user can come back to waiting work without watching the tab.
Not looked at: How the poll loop and card selection work in parallax/web/app.js beyond the hash and state lines. The test helpers and fixtures in tests/test_ui_browser.py beyond the test names.

kind: feature
size: small
title: notifying when a task is ready or needs the user
scope: parallax/web/app.js, tests/test_ui_browser.py
budget: 5.00

## Problem
The page shows tasks that are Ready or need the user only if the tab is in view. A person who works in another window has no signal that a task is waiting. The state test is at parallax/web/app.js:317, where `ready` and `needs you` both count as waiting. Nothing in the page uses `window.Notification`.

The page reads only the token from the URL hash. It does `location.hash.slice(1)` at parallax/web/app.js:648 and stores it at parallax/web/app.js:649. A hash of the form `#TOKEN&task=TASKID` would currently be read whole as the token. There is no link that opens a given task's card.

tests/test_ui_browser.py has no test that stubs `window.Notification`.

## Outcome
1. asked: When a task changes to Ready or to needs you, the page creates one browser notification for that task. (not browser-testable)
2. asked: Clicking the notification opens that task's card, by going to a link of the form `#TOKEN&task=TASKID` with both after the #. (not browser-testable)
3. asked: Opening a link of the form `#TOKEN&task=TASKID` directly loads the page signed in with TOKEN and opens the card for TASKID.
4. asked: The page asks for notification permission once, the first time a notification is needed, and not again after that. (not browser-testable)
5. asked: Tasks that are already Ready or needs you when the page loads do not notify.
6. asked: Each change notifies once. A later poll that sees the same state again does not notify again.
7. inferred: The token is still read correctly from a hash with no `&task=` part, and it is saved to session storage as before.
8. inferred: When permission is denied or `window.Notification` is missing, the page keeps working and shows no error.
9. asked: tests/test_ui_browser.py has tests with a stubbed `window.Notification` that prove outcomes 1, 2, 4, 5 and 6, and a test that opens the `#TOKEN&task=TASKID` link and sees the card.

## Constraints
- Only parallax/web/app.js and tests/test_ui_browser.py change.
- No new settings, no new controls and no new storage keys beyond what the page already uses.
- Existing hash links of the form `#TOKEN` keep working.
- Existing card behavior, keyboard shortcuts and polling do not change.
- The existing tests in tests/test_ui_browser.py keep passing.
- Budget for the work is $5.
