"""The UI in a real browser (headless Chromium through Playwright), every flow a person uses.

No model and no cost: agents are fakes, and a task's life is played into the ledger. Skipped,
with the reason, where Playwright or Chromium isn't installed (inside a task's sandbox, say).
"""
import os
import re
import threading
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="needs Playwright: uv run --with playwright")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

from fakes import FakeChecker, FakeDrafter, ScriptedAgent, good_probe, junit_runner  # noqa: E402
from parallax import build, lifecycle, pilot, preflight, views  # noqa: E402
from parallax.agents.base import Review  # noqa: E402
from parallax.core import Project  # noqa: E402
from parallax.ui import ERROR_MESSAGE, UI  # noqa: E402
from test_lifecycle_gates import docs, make_key  # noqa: E402

WAIT = 12_000  # ms: the page polls every 2s


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:  # inside Parallax's check, the UI tester's pinned browser (see testrun.py)
            b = p.chromium.launch(executable_path=os.environ.get("PARALLAX_BROWSER") or None)
        except Exception as err:  # the browser or its system libraries aren't installed here
            pytest.skip(f"Chromium can't start: {str(err).splitlines()[0]}. "
                        "See README: sudo apt-get install -y libnss3 libnspr4 libasound2t64")
        yield b
        b.close()


@pytest.fixture
def proj(repo, monkeypatch):
    make_key()
    monkeypatch.setattr(build, "_spawn", lambda argv, env, cwd, log: 9)
    monkeypatch.setattr(preflight, "run_srt", good_probe)  # a raise runs preflight; no sandbox in a sandbox
    (repo / "README.md").write_text("# calc\n\nA calculater.\n")
    import subprocess
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "readme"], check=True)
    return Project.init(repo)


@pytest.fixture
def server(proj):
    app = UI(proj.root, port=0)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    yield app
    app.close()


TRACES = os.environ.get("PARALLAX_BROWSER_TRACES")  # a folder: failed tests leave a trace and console log there (CI uploads it)


@pytest.fixture
def page(browser, server, request):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    errors, console = [], []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    if TRACES:
        ctx.tracing.start(screenshots=True, snapshots=True, sources=True)
    pg.goto(server.url)
    yield pg
    failed = getattr(getattr(request.node, "rep_call", None), "failed", False)
    if TRACES:
        folder = Path(TRACES)
        folder.mkdir(parents=True, exist_ok=True)
        name = re.sub(r"[^\w.-]", "_", request.node.name)
        ctx.tracing.stop(path=str(folder / f"{name}.trace.zip") if failed else None)
        if failed:
            (folder / f"{name}.console.txt").write_text("\n".join(console + [f"pageerror: {e}" for e in errors]) + "\n")
    ctx.close()
    assert errors == [], errors  # no script errors in any flow


def titled(title, outcome="The README reads right."):
    d = docs()
    d["intent"] = d["intent"].replace("fixing the README install steps", title).replace(
        "A new user on WSL can follow them.", outcome)
    return d


def run_to_ready(proj, title="fixing the README", steps=None, review=None, work="fix the README"):
    tid = pilot.intake(proj, work)["task"]
    maker = ScriptedAgent(steps=steps or [("write", "README.md", "# calc\n\nA calculator.\n")])
    status = build.run_mode(proj, tid, "pilot", FakeDrafter(titled(title)), lambda left, s: maker,
                            FakeChecker(reviews=[review or Review("pass")]), test_runner=junit_runner(),
                            preflight_runner=good_probe)
    return tid, status


def launched(proj, title):
    """Drafted and approved by the launch rule, parked before the build."""
    tid = pilot.intake(proj, title)["task"]
    pilot.draft_until_fit(proj, tid, FakeDrafter(titled(title)))
    lifecycle.approve(proj, tid, rule=pilot.launch_rule(proj, tid)[1])
    return tid


def stopped(proj, tid, why, **data):
    """The pilot stops on something for you, and its process ends, as a real one does."""
    proj.ledger.append("stuck.raised", "parallax", why, task=tid, **data)
    proj.ledger.append("builder.finished", "parallax", "", task=tid, status="stuck")


def row(page, tid):
    return page.locator(f'.row[data-task="{tid}"]')


def open_card(page, tid):
    row(page, tid).click(timeout=WAIT)
    expect(page.locator("#card .card-head")).to_contain_text(tid, timeout=WAIT)
    expect(page.locator("#card-title")).to_be_focused()


def contrast(fg: str, bg: str) -> float:
    def lum(c):
        r, g, b = [int(x) / 255 for x in re.findall(r"\d+", c)[:3]]
        f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in (r, g, b)]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# first launch, and typing work in -------------------------------------------------------------------

def test_first_launch_says_what_to_do(page):
    expect(page.locator("#queue")).to_contain_text("Type the work above and press Enter")
    page.keyboard.press("/")
    expect(page.locator("#work")).to_be_focused()


def test_typing_work_in_starts_a_task_you_can_see(page, proj):
    page.keyboard.press("/")
    page.keyboard.type("fix the typo in the README")
    page.keyboard.press("Enter")
    expect(page.locator("#status")).to_contain_text("on it")
    expect(page.locator("#work")).to_have_value("")
    tid = next(iter(proj.tasks()))
    expect(row(page, tid)).to_contain_text("Focus writing the intent")
    assert proj.task(tid)["status"] == "drafting"


def test_empty_work_is_not_sent(page, proj):
    page.fill("#work", "   ")
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert proj.tasks() == {}


# a task moving through each state, and which agent has it ----------------------------------------------

def test_a_task_moves_through_every_state_live_and_says_who_has_it(page, proj):
    tid = launched(proj, "adding a usage example")
    expect(row(page, tid)).to_contain_text("preparing the build", timeout=WAIT)
    steps = [("maker.started", {"stage": "build"}, "Maker building"),
             ("check.started", {}, "running the plan's tests"),
             ("tests.recorded", {"passed": 3, "total": 3, "exit": 0, "per_file": {}, "tree": "", "harness_reset": []}, "Second Eye reviewing the change"),
             ("rework.started", {"cycle": 1}, "Maker reworking (1 of 3)")]
    for kind, data, says in steps:
        proj.ledger.append(kind, "parallax", "the link points at a missing file" if kind == "rework.started" else "",
                           task=tid, **data)
        expect(row(page, tid)).to_contain_text(says, timeout=WAIT)
    expect(row(page, tid)).to_contain_text("The link points at a missing file")
    expect(row(page, tid)).to_contain_text("of $2.20")  # spent against the cap, raised to the floor
    open_card(page, tid)
    expect(page.locator("#card .live")).to_contain_text("Maker reworking (1 of 3)")


def test_several_tasks_at_once_riskiest_first_and_ready_last(page, proj):
    ready, _ = run_to_ready(proj, "stating supported Python versions")
    secret, st = run_to_ready(proj, "adding WSL install steps",
                              steps=[("write", "README.md", "# calc\n"), ("write", ".env", "API_KEY=sk-live-1\n")])
    assert st == "disputed"
    cap = launched(proj, "rewriting the install section")
    stopped(proj, cap, "the budget cap ran out ($2.10 of $2.00 estimated)", budget=True)
    working = launched(proj, "adding a license badge")
    expect(page.locator("#count")).to_have_text("3 waiting on you", timeout=WAIT)
    order = page.locator("#queue section").first.locator(".row").evaluate_all("rs => rs.map(r => r.dataset.task)")
    assert order == [secret, cap, ready]
    expect(row(page, secret)).to_contain_text("Secrets file")
    expect(row(page, working)).to_be_visible()
    assert page.title() == "(3) Parallax"


# the cards ---------------------------------------------------------------------------------------------

def test_a_ready_card_reads_in_one_pass(page, proj):
    tid, _ = run_to_ready(proj, review=Review("pass", [], "whether pip is on PATH in a fresh WSL distro"))
    open_card(page, tid)
    card = page.locator("#card")
    expect(page.locator("#card-title")).to_have_text("fixing the README")
    expect(card.locator(".bottom")).to_have_text("Second Eye passed and 3 of 3 plan tests pass.")
    expect(card).to_contain_text("whether pip is on PATH in a fresh WSL distro")  # the gap itself, not a pointer
    expect(card).not_to_contain_text("see Found")
    expect(card).not_to_contain_text("the work:")
    expect(card).not_to_contain_text("parallax accept")  # the buttons say it
    expect(card.get_by_role("button", name="Accept")).to_have_class(re.compile("primary"))
    page.keyboard.press("d")
    expect(page.locator("#doc")).to_contain_text("+A calculator.")


def test_a_needs_you_card_shows_the_files_and_whether_a_secret_has_content(page, proj):
    tid, _ = run_to_ready(proj, steps=[("write", "README.md", "# calc\n"), ("write", ".env", "API_KEY=sk-1\n"),
                                       ("write", "notes.txt", "")])
    open_card(page, tid)
    card = page.locator("#card")
    expect(card.locator(".bottom")).to_have_text(".env looks like a secrets file and has content (13 bytes).")
    expect(card.locator(".question")).to_contain_text("secrets file with content")
    expect(card.locator("tr.risky")).to_contain_text("has content, 13 bytes")
    expect(card.locator(".files")).not_to_contain_text("notes.txt")  # empty and outside the plan: removed by code, not asked
    expect(card).to_contain_text("Whose call: security.")
    expect(card).to_contain_text("the plan's tests and Second Eye (the blind checker) haven't run")
    expect(card.locator("#opt-reject")).to_have_class(re.compile("primary"))


def test_a_background_error_leads_with_the_error_and_a_repeat_says_drop(page, proj):
    tid = launched(proj, "adding a contributing guide")
    why = "error: the sandbox runtime exited with code 1 (srt: bwrap: No permissions to create new namespace)"
    stopped(proj, tid, why, error=True)
    open_card(page, tid)
    expect(page.locator("#card .bottom")).to_have_text("The sandbox runtime exited with code 1.", timeout=WAIT)
    expect(page.locator("#opt-retry")).to_have_class(re.compile("primary"))
    page.locator("#opt-retry").click()  # retry, and it fails the same way
    expect(page.locator("#status")).not_to_have_text("", timeout=WAIT)
    stopped(proj, tid, why, error=True)
    open_card(page, tid)
    expect(page.locator("#opt-drop")).to_have_class(re.compile("primary"), timeout=WAIT)


def test_a_task_at_its_cap_offers_the_raise(page, proj):
    tid = launched(proj, "rewriting the install section")
    stopped(proj, tid, "the budget cap ran out ($2.10 of $2.00 estimated)", budget=True, cost_usd=2.1)
    open_card(page, tid)
    expect(page.locator("#card .question")).to_contain_text("Raise the cap to $")
    page.locator("#opt-raise").click()
    expect(page.locator("#status")).to_contain_text("without you", timeout=WAIT)
    expect(row(page, tid)).to_contain_text("of $3.20", timeout=WAIT)  # the raised cap
    assert [e for e in proj.ledger.entries() if e["kind"] == "budget.raised"]


# deciding -----------------------------------------------------------------------------------------------

def test_accept_is_two_keys_and_shows_the_merge_command(page, proj):
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.keyboard.press("a")
    expect(page.locator("#opt-accept")).to_be_focused()
    assert proj.task(tid)["status"] == "ready"  # one key never decides
    page.keyboard.press("Enter")
    expect(page.locator("#merge")).to_contain_text("git merge --ff-only", timeout=WAIT)
    assert proj.task(tid)["status"] == "accepted"
    expect(page.locator("details.done")).to_contain_text("accepted, the merge is yours")


def test_reject_needs_a_reason_survives_live_updates_then_redrafts_and_comes_back(page, proj):
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.keyboard.press("r")
    expect(page.locator("#reason")).to_be_focused()
    page.get_by_role("button", name="Reject and redraft").click()
    expect(page.locator("#status")).to_contain_text("needs a reason")
    page.locator("#reason").type("also name the shell")
    proj.ledger.append("note", "parallax", "something else happened", task="other")  # a live update lands
    page.wait_for_timeout(4500)
    expect(page.locator("#reason")).to_have_value("also name the shell")
    expect(page.locator("#reason")).to_be_focused()
    page.keyboard.press("Enter")
    expect(row(page, tid)).to_contain_text("Focus writing the intent", timeout=WAIT)
    assert [e["reason"] for e in proj.ledger.entries() if e["kind"] == "task.redraft"] == ["also name the shell"]
    maker = ScriptedAgent(steps=[("write", "README.md", "# calc\n\nA calculator, in bash.\n")])  # the redraft runs
    assert build.run_mode(proj, tid, "pilot", FakeDrafter(titled("fixing the README")), lambda left, s: maker,
                          FakeChecker(), test_runner=junit_runner(), preflight_runner=good_probe) == "ready"
    expect(row(page, tid)).to_contain_text("Ready", timeout=WAIT)
    open_card(page, tid)
    expect(page.locator("#card .redraft")).to_have_text("Redrafted after you rejected it")
    lead = page.locator("#card h3").first
    expect(lead).to_have_text("Since you rejected it")  # the first section, above the decision
    expect(page.locator("#card ul.plain").first).to_contain_text('you rejected the last version: "also name the shell"')
    assert page.locator("#card h3").first.bounding_box()["y"] < page.locator("#card .decide").bounding_box()["y"]


def test_escape_keeps_a_half_typed_reason(page, proj):
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.keyboard.press("r")
    page.keyboard.type("half a thought")
    page.keyboard.press("Escape")
    expect(page.locator("#reason")).to_have_count(0)
    page.keyboard.press("r")
    expect(page.locator("#reason")).to_have_value("half a thought")


def test_drop_ends_the_task(page, proj):
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.locator("#opt-drop").click()
    page.locator("#reason").fill("not needed after all")
    page.get_by_role("button", name="Drop it").click()
    expect(page.locator("details.done")).to_contain_text("dropped", timeout=WAIT)
    assert proj.task(tid)["status"] == "rejected"


def test_after_a_decision_the_next_waiting_card_opens(page, proj):
    first, _ = run_to_ready(proj, "first thing")
    second, _ = run_to_ready(proj, "second thing")
    open_card(page, first)
    page.locator("#opt-drop").click()
    page.locator("#reason").fill("not needed")
    page.keyboard.press("Enter")
    expect(page.locator("#card-title")).to_have_text("second thing", timeout=WAIT)


# keyboard only, a narrow window, coming back --------------------------------------------------------------

def test_keyboard_only(page, proj):
    ready, _ = run_to_ready(proj, "first thing")
    working = launched(proj, "still working")
    # the page polls every two seconds: wait until it shows both tasks as they are in the ledger, the first one
    # Ready and the second under Working, before a key is pressed. A row alone can be a stale copy (CI, twice).
    expect(row(page, ready).locator(".tag.good")).to_be_visible(timeout=WAIT)
    expect(row(page, working)).to_be_visible(timeout=WAIT)
    page.keyboard.press("Tab")
    expect(page.locator("#work")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#work")).not_to_be_focused()  # Escape blurred the box: keys go to the page now
    page.keyboard.press("n")
    expect(page.locator("#card-title")).to_be_focused(timeout=WAIT)  # the card is fetched first; a slow runner takes seconds
    expect(page.locator("#card-title")).to_have_text("first thing")
    page.keyboard.press("j")
    expect(page.locator("#card-title")).to_have_text("still working", timeout=WAIT)
    page.keyboard.press("Escape")
    expect(row(page, working)).to_be_focused()
    page.keyboard.press("k")
    expect(page.locator("#card-title")).to_have_text("first thing", timeout=WAIT)
    page.keyboard.press("2")
    expect(page.locator("#opt-reject")).to_be_focused()


def test_focus_holds_across_polls(page, proj):
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.locator('[data-focus="doc-plan"]').focus()
    page.wait_for_timeout(5000)
    expect(page.locator('[data-focus="doc-plan"]')).to_be_focused()


def test_a_narrow_window_shows_the_list_or_one_card(browser, server, proj):
    tid, _ = run_to_ready(proj, steps=[("write", "README.md", "# calc\n"), ("write", ".env", "K=1\n")])
    for width in (390, 320):
        ctx = browser.new_context(viewport={"width": width, "height": 800})
        pg = ctx.new_page()
        pg.goto(server.url)
        open_card(pg, tid)
        expect(pg.locator("#card-title")).to_be_in_viewport()
        expect(pg.locator("#queue")).to_be_hidden()
        wide = pg.evaluate("[...document.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => e.tagName + '.' + e.className + '#' + e.id).slice(0, 8)")
        assert pg.evaluate("document.documentElement.scrollWidth") <= width, wide
        pg.get_by_role("button", name="Back to tasks").click()
        expect(row(pg, tid)).to_be_focused()
        assert pg.evaluate("document.documentElement.scrollWidth") <= width
        ctx.close()


def test_leaving_and_coming_back(browser, server, proj):
    tid, _ = run_to_ready(proj)
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(server.url)
    expect(row(pg, tid)).to_be_visible()
    pg.reload()
    expect(row(pg, tid)).to_be_visible()  # the same tab keeps working
    assert pg.url == server.url  # the link stays in the address bar, ready to bookmark
    ctx.close()
    again = UI(proj.root)  # parallax ui restarted: the same link
    assert again.token == server.token
    again.server.server_close()
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(server.url.split("#")[0])
    expect(pg.locator("#locked")).to_be_visible()
    expect(pg.locator("#intake")).to_be_hidden()
    ctx.close()


def test_a_server_that_goes_away_says_so(browser, proj):
    app = UI(proj.root, port=0)
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(app.url)
    expect(pg.locator("#queue")).to_be_visible()
    app.close()
    expect(pg.locator("#offline")).to_be_visible(timeout=WAIT)
    ctx.close()


def test_a_server_error_is_shown_not_swallowed(page, proj, monkeypatch):
    tid, _ = run_to_ready(proj)

    def broken(project, task_id):
        raise RuntimeError("the card broke")
    monkeypatch.setattr(views, "card", broken)
    row(page, tid).click()
    expect(page.locator("#status")).to_contain_text(ERROR_MESSAGE, timeout=WAIT)  # one fixed line; the detail stays in the terminal
    expect(page.locator("#status")).not_to_contain_text("the card broke")


def test_dark_mode_keeps_the_buttons_readable(browser, server, proj):
    tid, _ = run_to_ready(proj)
    ctx = browser.new_context(color_scheme="dark")
    pg = ctx.new_page()
    pg.goto(server.url)
    open_card(pg, tid)
    accept = pg.locator("#opt-accept")
    fg, bg = accept.evaluate("b => [getComputedStyle(b).color, getComputedStyle(b).backgroundColor]")
    assert contrast(fg, bg) >= 4.5, (fg, bg)
    ctx.close()


def test_agent_text_is_never_html(page, proj):
    tid, _ = run_to_ready(proj, "<img src=x onerror=alert(1)> title")
    expect(row(page, tid)).to_contain_text("<img src=x onerror=alert(1)> title", timeout=WAIT)
    assert page.locator("#queue img").count() == 0


# the agents' portraits: motion means status --------------------------------------------------------------

def test_motion_follows_state_and_portraits_only_move_while_working(page, proj):
    """Working animates, waiting and done are still, a stage that just finished hops once; hover hops once."""
    assert page.evaluate("[window.parallaxMotion(null,'working'), window.parallaxMotion('working','done'), "
                         "window.parallaxMotion('done','done'), window.parallaxMotion(null,'waiting'), window.parallaxMotion('working','waiting')]") \
        == ["working", "hop", "still", "still", "hop"]
    expect(page.locator("#logo svg")).to_have_count(1)  # the pixel P, inlined from /brand.json
    assert page.evaluate("document.querySelectorAll('#logo .layer').length") == 2
    tid = launched(proj, "adding a badge")
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    working = row(page, tid).locator(".portrait.working")
    expect(working).to_have_count(1, timeout=WAIT)
    expect(working).to_have_attribute("title", "Maker, builds in the sandbox")
    assert page.evaluate("getComputedStyle(document.querySelector('.portrait.working .bust')).animationName") == "bob"
    assert page.evaluate("getComputedStyle(document.querySelector('.portrait.working .glow')).animationName") == "pulse"
    open_card(page, tid)
    stages = page.locator("#card .stages .stage")
    expect(stages).to_have_count(3)
    expect(stages.nth(0)).to_have_class(re.compile("done")) and expect(stages.nth(0)).to_contain_text("Focus")
    expect(stages.nth(1)).to_have_class(re.compile("working")) and expect(stages.nth(1)).to_contain_text("Maker")
    expect(stages.nth(2)).to_have_class(re.compile("waiting")) and expect(stages.nth(2)).to_contain_text("Second Eye")
    assert page.evaluate("getComputedStyle(document.querySelector('.stage.waiting .bust')).animationName") == "none"
    # the stage finishes: Maker hops once, then is still; nothing loops on a task that waits on you
    proj.ledger.append("build.finished", "parallax", "", task=tid, status="built")
    proj.ledger.append("check.started", "parallax", "", task=tid)
    proj.ledger.append("tests.recorded", "parallax", "", task=tid, passed=3, total=3, exit=0, per_file={}, tree="", harness_reset=[])
    expect(page.locator("#card .stage.done .portrait").nth(1)).to_have_class(re.compile("hop|still"), timeout=WAIT)
    expect(page.locator("#card .stage.done .portrait").nth(1)).to_have_class(re.compile("still"), timeout=WAIT)
    expect(page.locator("#card .stage.working")).to_contain_text("Second Eye")
    assert page.evaluate("document.querySelectorAll('#card .portrait.working').length") == 1


def test_reduced_motion_keeps_every_portrait_still(browser, server, proj):
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
    pg = ctx.new_page()
    pg.goto(server.url)
    tid = launched(proj, "adding a badge")
    proj.ledger.append("maker.started", "parallax", "", task=tid, stage="build")
    expect(row(pg, tid).locator(".portrait.working")).to_have_count(1, timeout=WAIT)
    assert pg.evaluate("getComputedStyle(document.querySelector('.portrait.working .bust')).animationName") == "none"
    assert pg.evaluate("getComputedStyle(document.querySelector('.portrait.working .glow')).animationName") == "none"
    ctx.close()


def test_n_opens_a_task_that_went_ready_since_the_pages_last_poll(page, proj):
    """The cause behind a flaky keyboard run: the page polls every two seconds, so right after a task
    goes Ready its row can still sit under Working in the page's copy of the board. n must fetch the
    board and open the card, not drop the key."""
    working = launched(proj, "still working")
    expect(row(page, working)).to_be_visible(timeout=WAIT)
    page.evaluate("clearTimeout; state.board.waiting = []; state.board.count = 0; renderQueue()")  # the stale copy: nothing waits
    ready, _ = run_to_ready(proj, "first thing")  # the ledger moves on; the page hasn't polled yet
    assert page.evaluate("state.board.waiting.length") == 0
    page.keyboard.press("n")
    expect(page.locator("#card-title")).to_be_focused(timeout=WAIT)
    expect(page.locator("#card-title")).to_have_text("first thing")
