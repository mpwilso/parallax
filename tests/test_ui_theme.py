"""Light mode, dark mode and the accessibility basics, in a real browser: the system's theme by default,
a picker kept in this browser only, AA contrast for body and status text in both themes, a name for
every stage symbol, a visible focus outline, and nothing moving under reduced motion."""
import threading

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="needs Playwright: uv run --with playwright")
from playwright.sync_api import expect  # noqa: E402

from parallax import outputs  # noqa: E402
from parallax.accept import accept  # noqa: E402
from parallax.ui import UI  # noqa: E402
import test_ui_browser as shared  # noqa: E402
from test_ui_browser import WAIT, launched, open_card, run_to_ready, stopped  # noqa: E402

browser, proj = shared.browser, shared.proj  # the same fixtures as the other browser tests

BG = {"light": "rgb(246, 245, 242)", "dark": "rgb(21, 21, 19)"}

CONTRAST = """(sel) => {
  const el = document.querySelector(sel);
  if (!el || !el.offsetParent) return null;
  const parse = c => (c.match(/[\\d.]+/g) || []).map(Number);
  const fg = parse(getComputedStyle(el).color);
  let alpha = fg.length > 3 ? fg[3] : 1;
  for (let n = el; n; n = n.parentElement) alpha *= parseFloat(getComputedStyle(n).opacity);
  let bg = [255, 255, 255];
  for (let n = el; n; n = n.parentElement) {
    const c = parse(getComputedStyle(n).backgroundColor);
    if (c.length && (c.length < 4 || c[3] > 0)) { bg = c.slice(0, 3); break; }
  }
  const mixed = fg.slice(0, 3).map((v, i) => v * alpha + bg[i] * (1 - alpha));
  const lum = ([r, g, b]) => [r, g, b].map(v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; })
    .reduce((s, v, i) => s + v * [0.2126, 0.7152, 0.0722][i], 0);
  const [hi, lo] = [lum(mixed), lum(bg)].sort((a, b) => b - a);
  return (hi + 0.05) / (lo + 0.05);
}"""


def serve(proj, find=None):
    app = UI(proj.root, port=0, find=find or (lambda tool: f"/usr/bin/{tool}"))
    threading.Thread(target=app.server.serve_forever, daemon=True).start()
    return app


def low(page, selectors):
    """Every selector shown whose text is under 4.5:1 against what's behind it."""
    out = {}
    for sel in selectors:
        ratio = page.evaluate(CONTRAST, sel)
        assert ratio is not None, f"{sel} isn't on the page"
        if ratio < 4.5:
            out[sel] = round(ratio, 2)
    return out


def test_the_page_follows_the_system_theme_and_the_picker_is_kept_in_this_browser(browser, proj):
    app = serve(proj)
    try:
        for scheme in ("light", "dark"):
            ctx = browser.new_context(color_scheme=scheme)
            pg = ctx.new_page()
            pg.goto(app.url)
            expect(pg.locator("#queue")).to_be_visible(timeout=WAIT)
            assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == BG[scheme]
            assert pg.locator("#theme").input_value() == "system"
            ctx.close()
        ctx = browser.new_context(color_scheme="dark")
        pg = ctx.new_page()
        pg.goto(app.url)
        expect(pg.locator("#queue")).to_be_visible(timeout=WAIT)
        pg.get_by_label("Color theme").select_option("light")
        assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == BG["light"]
        pg.reload()  # the choice survives a reload, in this browser only
        expect(pg.locator("#queue")).to_be_visible(timeout=WAIT)
        assert pg.locator("#theme").input_value() == "light"
        assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == BG["light"]
        assert pg.evaluate("localStorage.getItem('parallax-theme')") == "light"
        pg.get_by_label("Color theme").select_option("system")
        assert pg.evaluate("getComputedStyle(document.body).backgroundColor") == BG["dark"]
        assert pg.evaluate("localStorage.getItem('parallax-theme')") is None
        ctx.close()
    finally:
        app.close()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_body_and_status_text_meet_aa_contrast_in_both_themes(browser, proj, scheme):
    first, _ = run_to_ready(proj, "fixing the README")
    second, _ = run_to_ready(proj, "fixing the README again")  # changes the same file: a heads-up
    merging, _ = run_to_ready(proj, "linking the changelog", steps=[("write", "README.md", "# calc\n\nSee CHANGELOG.md.\n")])
    accept(proj, merging, merging=True)  # Accept and merge, still running: the Merging card
    failing = launched(proj, "supporting subtraction")
    kept = outputs.keep(proj, failing, "tests", "the whole output\n" * 40)
    stopped(proj, failing, "the plan's tests couldn't run (exit 4): 1 failed", stage="check", **kept)
    app = serve(proj, find=lambda tool: None if tool == "uv" else f"/usr/bin/{tool}")  # the missing-tool note
    ctx = browser.new_context(viewport={"width": 1280, "height": 860}, color_scheme=scheme)
    pg = ctx.new_page()
    try:
        pg.goto(app.url)
        expect(pg.locator("#tools")).to_be_visible(timeout=WAIT)
        assert low(pg, ["#tools p", "#tools .tool", "#project", "#idle .lead", "#idle .overview li",
                        ".row .row-body", ".tag"]) == {}
        open_card(pg, second)
        expect(pg.locator("#card .heads-up")).to_be_visible()
        assert low(pg, ["#card .bottom", "#card .heads-up", "#card .hint", "#card .meta", "#card a.cite",
                        "#card .st .name", "#card .tag", "#card li"]) == {}
        open_card(pg, merging)
        expect(pg.locator(".merging .question")).to_be_visible()
        assert low(pg, ["#card .merging .question", "#card .merging .hint", "#card .bottom"]) == {}
        open_card(pg, failing)
        expect(pg.locator("a.output-link")).to_be_visible()
        assert low(pg, ["#card a.output-link", "#card .question", "#card .does"]) == {}
    finally:
        ctx.close()
        app.close()


def test_every_stage_symbol_has_a_name(browser, proj):
    ready, _ = run_to_ready(proj, "fixing the README")
    turns = launched(proj, "supporting subtraction")
    stopped(proj, turns, "Maker used all 80 turns", turns=True)
    app = serve(proj)
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    try:
        pg.goto(app.url)
        open_card(pg, ready)
        marks = pg.locator("#card .strip .mark")
        labels = [marks.nth(i).get_attribute("aria-label") for i in range(marks.count())]
        assert labels and all(label and ": " in label for label in labels)
        expect(pg.get_by_role("img", name="Maker: done").first).to_be_visible()
        open_card(pg, turns)
        expect(pg.get_by_role("img", name="Maker: failed").first).to_be_visible()
        assert pg.locator(".row .strip .mark:not([aria-label])").count() == 0  # the queue's rows too
    finally:
        ctx.close()
        app.close()


def test_everything_you_can_tab_to_shows_a_focus_outline(browser, proj):
    tid, _ = run_to_ready(proj, "fixing the README")
    app = serve(proj)
    ctx = browser.new_context(viewport={"width": 1280, "height": 860})
    pg = ctx.new_page()
    try:
        pg.goto(app.url)
        open_card(pg, tid)
        seen, unmarked = set(), []
        for _ in range(60):
            pg.keyboard.press("Tab")
            info = pg.evaluate("""() => { const a = document.activeElement; const s = getComputedStyle(a);
                return {id: a.tagName + '#' + (a.id || a.className || a.textContent.trim().slice(0, 30)),
                        style: s.outlineStyle, width: parseFloat(s.outlineWidth), body: a === document.body}; }""")
            if info["body"] or info["id"] in seen:
                continue
            seen.add(info["id"])
            if info["style"] == "none" or info["width"] < 1:
                unmarked.append(info["id"])
        assert len(seen) > 10 and unmarked == []
    finally:
        ctx.close()
        app.close()


def test_the_merging_spinner_and_portraits_stay_still_under_reduced_motion(browser, proj):
    tid, _ = run_to_ready(proj, "fixing the README")
    accept(proj, tid, merging=True)
    app = serve(proj)
    try:
        for motion, want in (("no-preference", "spin"), ("reduce", "none")):
            ctx = browser.new_context(viewport={"width": 1280, "height": 860}, reduced_motion=motion)
            pg = ctx.new_page()
            pg.goto(app.url)
            open_card(pg, tid)
            expect(pg.locator(".merging .spinner")).to_be_visible()
            assert pg.evaluate("getComputedStyle(document.querySelector('.merging .spinner')).animationName") == want
            if motion == "reduce":
                moving = pg.evaluate("[...document.querySelectorAll('*')].filter(e => getComputedStyle(e).animationName !== 'none').length")
                assert moving == 0
            ctx.close()
    finally:
        app.close()
