"""The UI in a real browser (headless Chromium through Playwright), every flow a person uses.

No model and no cost: agents are fakes, and a task's life is played into the ledger. Skipped,
with the reason, where Playwright or Chromium isn't installed (inside a task's sandbox, say).
"""
import json
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
    app = UI(proj.root, port=0, find=lambda tool: f"/usr/bin/{tool}")  # fake agents: nothing to install
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
    expect(row(page, tid)).to_contain_text("Preparing the build.", timeout=WAIT)  # a whole sentence
    steps = [("maker.started", {"stage": "build"}, "Maker building"),
             ("check.started", {}, "Running the plan's tests"),
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
    expect(card.get_by_role("button", name="Accept", exact=True)).to_have_class(re.compile("primary"))
    expect(card.get_by_role("button", name="Accept and merge")).to_be_visible()  # beside it, never instead of it
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
    expect(card).to_contain_text("the plan's tests and Second Eye, which haven't run")  # inline: the one-action Next line left room
    expect(card.locator("#opt-reject")).to_have_class(re.compile("primary"))


def test_a_background_error_leads_with_the_error_and_a_repeat_says_drop(page, proj):
    tid = launched(proj, "adding a contributing guide")
    why = "error: the sandbox runtime exited with code 1 (srt: bwrap: No permissions to create new namespace)"
    stopped(proj, tid, why, error=True)
    open_card(page, tid)
    expect(page.locator("#card .bottom")).to_have_text("The sandbox runtime exited with code 1.", timeout=WAIT)
    card = page.locator("#card")
    expect(card).to_contain_text("Run parallax doctor to find the cause.")  # how to find the cause
    expect(card).to_contain_text('For the user-namespace step, see "Allow user namespaces" in docs/wsl.md.')
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
    expect(page.locator("details.done")).to_contain_text("Accepted, waiting for your merge")


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
    expect(page.locator("details.done")).to_contain_text("Dropped", timeout=WAIT)
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
    expect(page.locator("#opt-merge")).to_be_focused()
    page.keyboard.press("3")
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


def test_a_missing_program_shows_on_the_page_with_its_fix(browser, proj):
    """"uv not found" mid-task (2026-10-01): now it's named before any task runs, with the fix."""
    found = {"uv": None}
    app = UI(proj.root, port=0, find=lambda t: found.get(t, f"/usr/bin/{t}"))
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    ctx = browser.new_context()
    pg = ctx.new_page()
    pg.goto(app.url)
    note = pg.locator("#tools")
    expect(note).to_be_visible(timeout=WAIT)
    expect(note).to_contain_text("uv not found: Parallax's own setup needs it. fix: install it with "
                                 "curl -LsSf https://astral.sh/uv/install.sh | sh.")
    expect(note).to_contain_text('export PATH="$HOME/.local/bin:$PATH", then restart parallax ui from a new terminal.')
    found["uv"] = "/home/me/.local/bin/uv"  # installed: the next look finds it, and the note goes
    pg.reload()
    expect(pg.locator("#queue")).to_be_visible()
    expect(note).to_be_hidden()
    ctx.close()
    app.close()


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
    stages = page.locator("#card .strip .st")  # one strip: Focus, Reticle, Maker, Check, Ready
    expect(stages).to_have_count(5)
    assert stages.evaluate_all("ls => ls.map(l => [l.dataset.stage, l.dataset.state])") == [
        ["focus", "done"], ["reticle", "skipped"], ["maker", "working"], ["check", "skipped"], ["ready", "skipped"]]
    tile = page.locator("#card .tile .portrait")
    expect(tile).to_have_attribute("data-agent", "maker")  # the working stage's agent, in its square tile
    expect(tile).to_have_class(re.compile("working"))
    assert page.evaluate("document.querySelectorAll('#card .portrait').length") == 1  # no row of floating portraits
    # the check starts: the tile shows Second Eye at work, and it's the only thing on the card that moves
    proj.ledger.append("build.finished", "parallax", "", task=tid, status="built")
    proj.ledger.append("check.started", "parallax", "", task=tid)
    proj.ledger.append("tests.recorded", "parallax", "", task=tid, passed=3, total=3, exit=0, per_file={}, tree="", harness_reset=[])
    expect(tile).to_have_attribute("data-agent", "second_eye", timeout=WAIT)
    expect(stages.nth(2)).to_have_attribute("data-state", "done")
    expect(stages.nth(3)).to_have_attribute("data-state", "working")
    assert page.evaluate("document.querySelectorAll('#card .portrait.working').length") == 1
    # it's Ready and waits on you: Second Eye hops once in the tile, then is still; nothing loops
    proj.ledger.append("verdict.recorded", "checker", "", task=tid, stage="check", tree="", verdict="pass", findings=[])
    proj.ledger.append("check.finished", "parallax", "", task=tid, status="ready", tree="")
    expect(tile).to_have_class(re.compile("hop|still"), timeout=WAIT)
    expect(tile).to_have_class(re.compile("still"), timeout=WAIT)
    assert page.evaluate("document.querySelectorAll('.portrait.working').length") == 0


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


# the rows, the overview, and what the card lets you do (real use, 2026-09-30) -------------------------------

def test_rows_show_a_chip_the_strip_spend_and_age_and_done_rows_their_outcome(page, proj):
    ready, _ = run_to_ready(proj, "stating supported Python versions")
    working = launched(proj, "adding a license badge")
    proj.ledger.append("maker.started", "parallax", "", task=working, stage="build")
    r = row(page, ready)
    expect(r.locator(".tag")).to_have_text("Ready", timeout=WAIT)
    expect(r.locator(".st")).to_have_count(5)
    expect(r.locator(".spend")).to_contain_text("of $2.20")
    expect(r.locator(".age")).not_to_be_empty()
    w = row(page, working)
    expect(w.locator(".tag")).to_have_text("Working", timeout=WAIT)
    expect(w.locator(".tile .portrait.working")).to_have_attribute("data-agent", "maker")
    line = w.locator(".line").inner_text()
    assert line.endswith((".", "\u2026")) and line[0].isupper()  # a whole sentence, or cut at a word with an ellipsis
    open_card(page, ready)
    page.locator("#opt-accept").click()
    expect(page.locator("details.done .row")).to_contain_text("Accepted, waiting for your merge", timeout=WAIT)
    expect(page.locator("details.done .row")).to_contain_text("$0.")
    expect(page.locator("details.done .row")).to_contain_text("1 touch")


def test_with_no_card_open_the_page_says_how_its_going(page, proj):
    tid, _ = run_to_ready(proj)
    from parallax.accept import accept
    accept(proj, tid)
    idle = page.locator("#idle")
    expect(idle).to_contain_text("1 task finished. It needed you 1 time; the goal is once.", timeout=WAIT)
    expect(idle).to_contain_text("spent on agents in the last 7 days")
    expect(idle).to_contain_text("fixing the README: accepted, waiting for your merge on ")
    assert idle.locator("canvas, svg, img").count() == 0  # plain words, no charts


def test_accept_and_merge_merges_with_one_click_and_says_so(page, proj):
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.locator("#opt-merge").click()
    expect(page.locator("#status")).to_contain_text("nothing was pushed.", timeout=WAIT)
    assert proj.task(tid)["status"] == "merged"
    expect(page.locator("details.done .row")).to_contain_text("Merged", timeout=WAIT)


def test_a_ledger_id_on_the_card_is_a_quiet_link_to_its_entry(page, proj):
    tid, _ = run_to_ready(proj, review=Review("pass", [], "whether pip is on PATH"))
    open_card(page, tid)
    link = page.locator("#card a.cite").first
    entry = link.inner_text().removeprefix("ledger ")
    link.click()
    expect(page.locator("#doc")).to_contain_text(f'"id": "{entry}"', timeout=WAIT)
    expect(page.locator("#doc")).to_contain_text('"kind"')


def test_a_failure_line_links_to_the_whole_output(page, proj):
    from parallax import outputs
    tid = launched(proj, "supporting subtraction")
    full = "the real cause is up here\n" + "\n".join(f"line {i}" for i in range(100)) + "\n1 failed\n"
    kept = outputs.keep(proj, tid, "tests", full)
    proj.ledger.append("disagreement.raised", "parallax", "the plan's tests couldn't run (exit 4): 1 failed", task=tid,
                       stage="check", **kept)
    proj.ledger.append("builder.finished", "parallax", "", task=tid, status="disputed")
    open_card(page, tid)
    page.locator("#card a.output-link").first.click()
    expect(page.locator("#doc")).to_contain_text("the real cause is up here", timeout=WAIT)
    expect(page.locator("#card .docs + p.hint")).to_contain_text("shown only when its hash matches the ledger")


def test_the_ask_box_answers_from_the_record_and_changes_nothing(page, proj, monkeypatch):
    from parallax import ask
    from test_ask import FakeAsker
    asker = FakeAsker({"answer": "All 3 plan tests passed [tests].", "sources": ["tests"]})
    monkeypatch.setattr(ask, "ASKER", asker)
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.locator("#ask").fill("did the tests pass?")
    page.keyboard.press("Enter")
    expect(page.locator(".answers")).to_contain_text("All 3 plan tests passed [tests].", timeout=WAIT)
    expect(page.locator(".answers")).to_contain_text("From: tests")
    expect(page.locator(".ask .hint")).to_contain_text("$0.02 of $0.25 used")
    assert proj.task(tid)["status"] == "ready" and "## plan" in asker.calls[0][1]


def test_a_stalled_cap_card_recommends_sending_it_back_with_a_note(page, proj):
    tid = launched(proj, "supporting subtraction")
    for _ in range(2):
        proj.ledger.append("tests.recorded", "parallax", "", task=tid, tree="t", exit=1,
                           per_file={"tests/test_readme.py": [1, 3, 0]}, passed=1, total=3, harness_reset=[])
    stopped(proj, tid, "the budget cap ran out ($2.30 of $2.20 estimated)", budget=True)
    expect(row(page, tid).locator(".tag")).to_have_text("Failed", timeout=WAIT)
    open_card(page, tid)
    expect(page.locator("#card .question")).to_contain_text("The last two checks failed the same way")
    back = page.locator("#opt-send-back")
    expect(back).to_have_text("Send back with a note") and expect(back).to_have_class(re.compile("primary"))
    back.click()
    page.locator("#reason").fill("subtraction needs its own test file first")
    page.get_by_role("button", name="Send it back").click()
    expect(page.locator("#status")).to_contain_text("redrafting", timeout=WAIT)


# coming back to work that waits: a notification, and a link to one card ------------------------------------

# window.Notification, stubbed before the page's own scripts run: every notification is recorded on the
# page, with the permission the test wants. permission null is a browser with no notifications at all.
NOTE_STUB = """
(permission => {
  window.__notes = [];   // the notifications the page made, in order
  window.__asked = 0;    // how many times it asked for permission
  function Fake(title, options) {
    const o = options || {};
    this.title = title;
    this.body = o.body;
    this.tag = o.tag;
    this.onclick = null;
    window.__notes.push(this);
  }
  Fake.permission = permission;
  Fake.requestPermission = () => { window.__asked += 1; return Promise.resolve(Fake.permission); };
  window.Notification = permission ? Fake : undefined;
})(%s);
"""


@pytest.fixture
def notify_page(browser, server):
    """A page with window.Notification stubbed, and whatever the link's fragment should say.

    Only these tests stub it, so every other flow still runs against the browser's own notifications."""
    made = []

    def open_page(permission="granted", tail=""):
        ctx = browser.new_context(viewport={"width": 1280, "height": 860})
        ctx.add_init_script(NOTE_STUB % json.dumps(permission))
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(server.url + tail)
        made.append((ctx, errors))
        return pg

    yield open_page
    for ctx, errors in made:
        ctx.close()
        assert errors == [], errors


def goes_ready(proj, tid):
    """The check passes in the ledger: the task turns Ready, as a finished run leaves it."""
    proj.ledger.append("verdict.recorded", "checker", "", task=tid, stage="check", tree="", verdict="pass", findings=[])
    proj.ledger.append("check.finished", "parallax", "", task=tid, status="ready", tree="")


def seen_working(pg, tid):
    """Wait until the page's own copy of the board has this task Working, before its state changes."""
    expect(row(pg, tid).locator(".tag")).to_have_text("Working", timeout=WAIT)


def notes(pg):
    return pg.evaluate("window.__notes.map(n => [n.title, n.body, n.tag])")


def test_a_task_turning_ready_notifies_you_once(notify_page, proj):
    pg = notify_page()
    tid = launched(proj, "adding a usage example")
    seen_working(pg, tid)
    goes_ready(proj, tid)
    expect(row(pg, tid).locator(".tag.good")).to_be_visible(timeout=WAIT)
    pg.wait_for_function("() => window.__notes.length > 0", timeout=WAIT)
    assert notes(pg) == [["Ready for you", "adding a usage example", tid]]


def test_a_task_that_starts_needing_you_notifies_you(notify_page, proj):
    pg = notify_page()
    tid = launched(proj, "rewriting the install section")
    seen_working(pg, tid)
    stopped(proj, tid, "the budget cap ran out ($2.10 of $2.00 estimated)", budget=True)
    expect(row(pg, tid).locator(".tag")).to_have_text("Failed", timeout=WAIT)
    pg.wait_for_function("() => window.__notes.length > 0", timeout=WAIT)
    assert notes(pg) == [["Needs you", "rewriting the install section", tid]]


def test_clicking_the_notification_opens_that_card(notify_page, proj, server):
    pg = notify_page()
    tid = launched(proj, "stating supported Python versions")
    seen_working(pg, tid)
    goes_ready(proj, tid)
    pg.wait_for_function("() => window.__notes.length > 0", timeout=WAIT)
    pg.evaluate("window.__notes[0].onclick()")
    expect(pg.locator("#card .card-head")).to_contain_text(tid, timeout=WAIT)
    expect(pg.locator("#card-title")).to_have_text("stating supported Python versions")
    assert pg.evaluate("location.hash") == f"#{server.token}&task={tid}"  # a link back to this card


def test_permission_is_asked_once_a_page_and_not_again(notify_page, proj):
    pg = notify_page(permission="default")
    first = launched(proj, "adding a usage example")
    second = launched(proj, "rewriting the install section")
    seen_working(pg, first)
    seen_working(pg, second)
    goes_ready(proj, first)
    pg.wait_for_function("() => window.__asked > 0", timeout=WAIT)
    stopped(proj, second, "the budget cap ran out ($2.10 of $2.00 estimated)", budget=True)
    expect(row(pg, second).locator(".tag")).to_have_text("Failed", timeout=WAIT)
    pg.wait_for_timeout(2500)  # another poll, another change: it doesn't ask twice
    assert pg.evaluate("window.__asked") == 1
    assert notes(pg) == []  # permission never granted: nothing was shown


def test_what_already_waits_on_you_when_the_page_loads_does_not_notify(notify_page, proj):
    ready, _ = run_to_ready(proj, "stating supported Python versions")
    stuck = launched(proj, "rewriting the install section")
    stopped(proj, stuck, "the budget cap ran out ($2.10 of $2.00 estimated)", budget=True)
    pg = notify_page()
    expect(pg.locator("#count")).to_have_text("2 waiting on you", timeout=WAIT)
    pg.wait_for_timeout(2500)  # a few more polls over the same board
    assert notes(pg) == []


def test_the_same_state_seen_again_does_not_notify_again(notify_page, proj):
    pg = notify_page()
    tid = launched(proj, "adding a usage example")
    seen_working(pg, tid)
    goes_ready(proj, tid)
    pg.wait_for_function("() => window.__notes.length > 0", timeout=WAIT)
    for i in range(2):  # more polls, the same state each time
        proj.ledger.append("note", "parallax", f"something else happened ({i})", task="other")
        pg.wait_for_timeout(2500)
    assert len(notes(pg)) == 1


def test_without_permission_the_page_works_as_ever_and_says_nothing(notify_page, proj):
    for permission in ("denied", None):  # refused, and a browser with no notifications at all
        pg = notify_page(permission=permission)
        tid = launched(proj, f"adding a license badge ({permission})")
        seen_working(pg, tid)
        goes_ready(proj, tid)
        expect(row(pg, tid).locator(".tag.good")).to_be_visible(timeout=WAIT)
        pg.wait_for_timeout(2500)
        assert notes(pg) == []
        open_card(pg, tid)  # as usable as ever, and no error shown
        assert pg.evaluate("document.querySelector('#status.error') === null")


def test_a_link_with_a_task_opens_that_card(notify_page, proj, server):
    tid, _ = run_to_ready(proj, "stating supported Python versions")
    pg = notify_page(tail=f"&task={tid}")
    expect(pg.locator("#card .card-head")).to_contain_text(tid, timeout=WAIT)
    expect(pg.locator("#card-title")).to_have_text("stating supported Python versions")
    expect(pg.locator("#card-title")).to_be_focused()
    assert pg.evaluate("sessionStorage.getItem('parallax-token')") == server.token


def test_a_link_with_only_the_token_still_signs_in(notify_page, proj, server):
    tid, _ = run_to_ready(proj)
    pg = notify_page()
    expect(row(pg, tid)).to_be_visible(timeout=WAIT)
    expect(pg.locator("#card")).to_be_hidden()
    assert pg.evaluate("sessionStorage.getItem('parallax-token')") == server.token


def test_the_merging_card_says_what_it_is_doing_and_its_buttons_cant_be_clicked(browser, server, proj, monkeypatch):
    """89bc50: for three minutes the card said "merging is yours" while the tests ran."""
    from parallax import accept as acc_mod
    policy = proj.root / "parallax.policy.toml"
    policy.write_text(policy.read_text().replace('test_command = ""              # Accept and merge',
                                                 'test_command = "scripts/test.sh"              # Accept and merge'))
    proj.reload_policy()
    go = threading.Event()

    def held(project, commit, command):  # the pre-merge run, held until the page has been looked at
        go.wait(30)
        return 0, "1 passed"
    monkeypatch.setattr(acc_mod, "TEST_RUNNER", held)
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, reduced_motion="reduce")
    page = ctx.new_page()
    page.goto(server.url)
    tid, _ = run_to_ready(proj)
    open_card(page, tid)
    page.locator("#opt-merge").click()
    card = page.locator("#card .merging")
    expect(card).to_contain_text("Merging: running the tests on the commit it would land", timeout=WAIT)
    expect(page.locator("#card")).not_to_contain_text("Merging is yours")
    for name in ("accept", "merge", "reject", "drop"):
        expect(page.locator(f"#opt-{name}")).to_be_disabled()
    expect(page.locator("#merge-elapsed")).not_to_be_empty()
    assert page.evaluate("getComputedStyle(document.querySelector('.spinner')).animationName") == "none"  # reduced motion
    expect(row(page, tid).locator(".tag")).to_have_text("Merging")
    go.set()
    expect(page.locator("details.done .row")).to_contain_text("Merged", timeout=WAIT)
    ctx.close()


def test_a_stopped_server_says_so_and_how_to_get_it_back(page):
    """Real use: the server died mid-session and the page said only "Failed to fetch"."""
    expect(page.locator("#queue")).to_be_visible()
    page.route("**/api/**", lambda route: route.abort())  # nothing answers, as when parallax ui has stopped
    expect(page.locator("#offline")).to_have_text(
        "Parallax isn't answering: the parallax ui server has stopped. Start it again with parallax ui in a terminal, "
        "then reload this page.", timeout=WAIT)
    page.locator("#work").fill("fixing the README")
    page.locator("#work").press("Enter")  # an action, too, says so, never "Failed to fetch"
    expect(page.locator("#status")).to_have_text(
        "Parallax isn't answering: the parallax ui server has stopped. Start it again with parallax ui in a terminal, "
        "then reload this page.", timeout=WAIT)


def test_the_strip_says_when_no_reticle_tests_counted(page, proj):
    """7ac365: Reticle showed a green tick though none of its tests loaded."""
    tid, _ = run_to_ready(proj)
    proj.ledger.append("reticle.recorded", "reticle", "0 tests kept, 1 weak ones dropped.", task=tid, kept=[],
                       weak=[{"name": "test_reticle", "why": "the file doesn't load on the base (collection failure)"}])
    open_card(page, tid)
    expect(page.locator("#card .strip")).to_contain_text("no Reticle tests counted", timeout=WAIT)
    expect(page.locator("#card")).to_contain_text("no Reticle tests counted.")


def test_a_task_that_overlaps_an_older_one_says_so_on_its_card_and_row(page, proj):
    """370571 and 40171b both reached Ready with the same change, and nothing said so."""
    first, _ = run_to_ready(proj, "adding the doctor hint")
    second, _ = run_to_ready(proj, "adding the doctor hint again")
    expect(row(page, second).locator(".heads-up")).to_have_text(f"Overlaps task {first}", timeout=WAIT)
    expect(row(page, first).locator(".heads-up")).to_have_count(0)
    open_card(page, second)
    expect(page.locator("#card .heads-up")).to_contain_text(f"Heads-up: task {first} (adding the doctor hint), Ready, also changes")
