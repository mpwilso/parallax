Bottom line: This task makes the page show a browser notification when a task becomes Ready or needs the user, and makes a `#TOKEN&task=TASKID` link open that task's card, so the user can come back to waiting work without watching the tab.
Not looked at: How the poll loop and card selection work in parallax/web/app.js beyond the hash and state lines. The test helpers and fixtures in tests/test_ui_browser.py beyond the test names.

## Steps
1. In parallax/web/app.js, add a small hash parser near the token read at line 648. Split `location.hash.slice(1)` on `&`. The first part is the token. A part that starts with `task=` gives the task id, decoded with `decodeURIComponent`. A hash with no `&task=` gives the same token as today, and it is stored in session storage as before (outcome 7).
2. In parallax/web/app.js, after the first load of tasks, open the card for the task id from the hash. Reuse the existing card open path. Do it once. If the id is not in the list, do nothing.
3. In parallax/web/app.js, add a `notifyWaiting(task)` helper. It returns at once if `window.Notification` is missing. If permission is `default`, it calls `Notification.requestPermission()` the first time a notification is needed and remembers that it asked in a module variable, so it asks once per page (outcome 4). If permission is `denied`, it does nothing. All of it sits in try/catch so nothing shows an error (outcome 8).
4. In parallax/web/app.js, keep a map of the last seen state per task id. On each poll, next to the waiting test at line 317, compare each task's state with the map. Notify only when the state is now `ready` or `needs you` and the earlier state was known and different. The first load only fills the map, so tasks already waiting do not notify (outcome 5). A repeat poll with the same state does not notify (outcome 6).
5. In parallax/web/app.js, the notification gets the task id as its tag and a short title and body. Its `onclick` focuses the window and sets `location.hash` to `TOKEN&task=TASKID`, using the stored token, then opens that card (outcome 2). No new storage keys, settings or controls.
6. In tests/test_ui_browser.py, add an init script that stubs `window.Notification` with a class that records each call, its permission, and its `requestPermission` calls on `window`. Add the tests listed below.

## Tests
New tests first, all in tests/test_ui_browser.py, using the stub:
- A task changes to Ready after load: exactly one notification is created for that task (outcome 1).
- A task changes to needs you after load: one notification is created (outcome 1).
- Calling the recorded notification's `onclick` sets the hash to `TOKEN&task=TASKID` and shows that card (outcome 2).
- With permission `default`, two notifications in a row call `requestPermission` once (outcome 4).
- Tasks already Ready or needs you on first load create no notification (outcome 5).
- Several polls with the same state create one notification, not more (outcome 6).
- Opening `#TOKEN&task=TASKID` directly loads signed in and shows the card for TASKID (outcome 3).
- Opening `#TOKEN` alone still signs in and writes the token to session storage (outcome 7).
- With permission `denied`, and with `window.Notification` deleted, a state change creates no notification and the page shows no error (outcome 8).

The existing tests in tests/test_ui_browser.py must keep passing, since they cover `#TOKEN` links, cards, shortcuts and polling.

## Risks
- A token that contains `&` would now be cut short. Tokens are expected to be URL safe, but the build should check this.
- The first-load map must be filled before the first notify check, or tasks already waiting would notify.
- The browser may drop a notification click handler if the page is closed. This is a limit of the platform and is not tested.
- If the stub is added to all tests, an existing test could change behavior. The build should add it only in the new tests.

```toml
files = ["parallax/web/app.js", "tests/test_ui_browser.py"]
tests = ["tests/test_ui_browser.py"]
lines_changed = 220
domains = []
outside_reads = []
binaries = []
symlinks = []
dependencies = []
review_tightening = ""
estimated_cost_usd = 1.90
budget_cap_usd = 5.00
covers = { "1" = ["tests/test_ui_browser.py"], "2" = ["tests/test_ui_browser.py"], "3" = ["tests/test_ui_browser.py"], "4" = ["tests/test_ui_browser.py"], "5" = ["tests/test_ui_browser.py"], "6" = ["tests/test_ui_browser.py"], "7" = ["tests/test_ui_browser.py"], "8" = ["tests/test_ui_browser.py"], "9" = ["tests/test_ui_browser.py"] }
user_flows = ["3"]
```
