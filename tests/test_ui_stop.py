"""Stop on a running card, asked once; and the intake box's heads-up for text that reads like build
steps rather than a task, which you can always send anyway."""
import subprocess
import sys
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="needs Playwright: uv run --with playwright")
from playwright.sync_api import expect  # noqa: E402

import test_ui_browser as shared  # noqa: E402
from parallax.ui import UI  # noqa: E402
from test_ui_browser import WAIT, launched, open_card  # noqa: E402

browser, proj = shared.browser, shared.proj  # the same fixtures as the other browser tests

WARNING = "This looks like a list of build steps. Send it as one task anyway?"
STEPS = ("Work on a new branch, cleanup, from master. Make one commit per step. 1. Budget modes, chosen once. "
         "2. Clean up merged tasks and delete the branch. 3. Light and dark mode. 4. Stop a task. "
         "Then push the branch and wait for CI. Don't merge.")


@pytest.fixture
def page(browser, proj):
    app = UI(proj.root, port=0, find=lambda tool: f"/usr/bin/{tool}")
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    pg.goto(app.url)
    expect(pg.locator("#queue")).to_be_visible(timeout=WAIT)
    yield pg
    ctx.close()
    app.close()


def test_a_running_card_has_stop_asked_once_then_it_waits_with_resume(page, proj):
    tid = launched(proj, "supporting subtraction")
    builder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    proj.ledger.append("build.started", "parallax", "", task=tid, pid=builder.pid, mode="build")
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    open_card(page, tid)
    page.locator("#stop").click()
    expect(page.locator(".stop-confirm .question")).to_have_text("Stop this task now?")
    expect(page.locator("#stop-confirm")).to_be_focused()
    page.locator("#stop-cancel").click()  # changed your mind: it keeps running
    expect(page.locator("#stop")).to_be_visible()
    assert builder.poll() is None
    page.locator("#stop").click()
    page.locator("#stop-confirm").click()
    expect(page.locator("#status")).to_contain_text(f"stopped {tid}. it waits for you: resume, send back or drop.", timeout=WAIT)
    assert builder.wait(timeout=10) is not None
    expect(page.locator("#opt-resume")).to_be_visible(timeout=WAIT)
    expect(page.locator("#opt-send-back")).to_be_visible()
    expect(page.locator("#opt-drop")).to_be_visible()
    expect(page.locator("#card .bottom")).to_contain_text("You stopped it.")
    [stop] = [e for e in proj.ledger.entries() if e["kind"] == "task.stopped"]
    assert stop["actor"] == "human" and stop["data"]["stage"] == "Maker building"


def test_text_that_reads_like_build_steps_gets_a_heads_up_you_can_send_past(page, proj):
    work = page.locator("#work")
    work.fill(STEPS)
    work.press("Enter")
    expect(page.locator("#intake-warning")).to_be_visible()
    expect(page.locator("#intake-warning")).to_contain_text(WARNING)
    assert not [t for t in proj.tasks().values() if t.get("intent")]  # nothing sent yet
    page.locator("#edit-it").click()
    expect(page.locator("#intake-warning")).to_be_hidden()
    expect(work).to_be_focused()
    work.press("Enter")
    expect(page.locator("#intake-warning")).to_be_visible()
    page.locator("#send-anyway").click()  # a heads-up, never a block
    expect(page.locator("#status")).to_contain_text(": on it.", timeout=WAIT)
    assert [t["goal"] for t in proj.tasks().values() if t.get("intent")] == [STEPS]


def test_an_ordinary_task_is_sent_without_a_heads_up(page, proj):
    page.locator("#work").fill("Fix the typo in the README: calculater should be calculator. 1. find it 2. fix it")
    page.locator("#work").press("Enter")
    expect(page.locator("#status")).to_contain_text(": on it.", timeout=WAIT)
    expect(page.locator("#intake-warning")).to_be_hidden()
