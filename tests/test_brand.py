"""The agents' names and faces, and the logo: one data source for the UI and docs/brand/."""
import json
import re
from pathlib import Path

from parallax import brand, live

ROOT = Path(__file__).resolve().parents[1]


def test_the_agents_have_names_and_roles_and_nothing_else_is_named():
    assert [a["name"] for a in brand.AGENTS.values()] == ["Focus", "Reticle", "Maker", "Second Eye", "Field"]  # a task's order
    assert brand.label("reticle", with_role=True) == "Reticle (writes tests of what you asked, before the build; Maker can't see or change them)"
    assert brand.names() == "Focus, Reticle, Maker, Second Eye and Field"
    assert brand.label("second_eye", with_role=True) == "Second Eye (the blind checker; sees only the result, never the making)"
    assert brand.label("field") == "Field"
    assert "scope" not in brand.AGENTS  # reserved for a future read-only investigator


def test_motion_follows_state():
    """Working animates; idle and waiting are still; a stage that just finished hops once."""
    assert brand.motion(None, "working") == "working"
    assert brand.motion("working", "working") == "working"
    assert brand.motion("working", "done") == "hop"
    assert brand.motion("working", "waiting") == "hop"
    assert brand.motion("done", "done") == "still"
    assert brand.motion(None, "waiting") == "still"
    assert brand.motion("hop", "done") == "still"


def test_every_portrait_is_sixteen_by_sixteen_with_known_letters():
    for key, p in brand.PORTRAITS.items():
        assert len(p["rows"]) == 16 and all(len(r) == 16 for r in p["rows"]), key
        letters = {c for r in p["rows"] for c in r} - {"."}
        assert letters <= set(p["colors"]) | set(brand.BASE), (key, letters - set(p["colors"]) - set(brand.BASE))
        assert set(p["glow"]) <= letters


def test_side_light_lightens_after_an_outline_and_darkens_before_one():
    """The shading rule, on Maker's row 7: k s s s s s s s s k. The first s follows the outline and is
    lit; the last s precedes it and is shaded; the middle ones keep the plain skin color."""
    row = {(x, y): (fill, glow) for x, y, fill, glow in brand.pixels("maker")}
    assert brand.PORTRAITS["maker"]["rows"][7] == "...kssssssssk..."
    skin = brand.BASE["s"]
    assert row[(4, 7)][0] == brand._mix(skin, "#ffffff", brand.LIGHT)
    assert row[(11, 7)][0] == brand._mix(skin, "#000000", brand.DARK)
    assert row[(7, 7)][0] == skin
    # outline, glint and glow pixels are never shaded; column 0 counts as lit, column 15 as shaded
    assert row[(3, 7)][0] == brand.BASE["k"]
    focus = {(x, y): (fill, glow) for x, y, fill, glow in brand.pixels("focus")}
    assert focus[(10, 6)] == (brand.BASE["w"], False)  # the glint stays white
    assert focus[(7, 11)] == (brand.PORTRAITS["focus"]["colors"]["x"], True)  # a glow pixel keeps its color and is marked
    second = {(x, y): fill for x, y, fill, _ in brand.pixels("second_eye")}
    m = brand.PORTRAITS["second_eye"]["colors"]["m"]  # row 3: ...kmmmmmmmmk...
    assert second[(4, 3)] == brand._mix(m, "#ffffff", brand.LIGHT) and second[(11, 3)] == brand._mix(m, "#000000", brand.DARK)
    assert second[(1, 13)] == brand.PORTRAITS["second_eye"]["colors"]["v"]  # v is a glow letter: never shaded
    assert brand._mix("#000000", "#ffffff", 0.5) == "#808080"


def test_the_edges_count_as_lit_on_the_left_and_shaded_on_the_right(monkeypatch):
    rows = ["s" * 16] + ["." * 16] * 15  # a synthetic row with no outline at all
    monkeypatch.setitem(brand.PORTRAITS, "edge", {"halo": "#ffffff", "glow": "", "colors": {}, "rows": rows})
    px = {(x, y): fill for x, y, fill, _ in brand.pixels("edge")}
    skin = brand.BASE["s"]
    assert px[(0, 0)] == brand._mix(skin, "#ffffff", brand.LIGHT) and px[(15, 0)] == brand._mix(skin, "#000000", brand.DARK)
    assert px[(8, 0)] == skin


def test_portraits_render_one_rect_per_pixel_on_a_tile_with_a_halo():
    svg = brand.portrait_svg("field")
    assert 'shape-rendering="crispEdges"' in svg and f'fill="{brand.TILE}"' in svg
    assert '<circle cx="8" cy="8.5" r="7.5" fill="#f2c94c" opacity="0.16"/>' in svg
    assert svg.count("<rect ") == len(brand.pixels("field")) + 1  # the pixels, and the tile
    assert 'aria-label="Field, the UI tester"' in svg


def test_the_logo_is_the_p_twice_offset():
    svg = brand.logo_svg()
    filled = sum(r.count("k") for r in brand.LOGO_ROWS)
    assert svg.count("<rect ") == 2 * filled and 'viewBox="0 0 14 16"' in svg
    assert 'class="layer l1" fill="#a47cf0" opacity="1.0"' in svg and 'class="layer l2" fill="#5ef2ff" opacity="0.85"' in svg
    assert '<rect x="2" y="3"' in svg and '<rect x="4" y="1"' in svg  # the first pixel of each layer, at its offset
    assert "parallax" in brand.logo_svg(word=True) and "monospace" in brand.logo_svg(word=True)
    moving = brand.logo_svg(animated=True)
    assert "@keyframes drift1" in moving and "@keyframes drift2" in moving and "prefers-reduced-motion" in moving
    assert "steps(2)" in moving and "animation" not in brand.logo_svg() and "animation" not in brand.logo_svg(word=True)


def test_the_readme_images_and_the_ui_come_from_the_same_data():
    """docs/brand/ holds the rendered files; the UI fetches the same rendering as /brand.json."""
    for name, render in brand.ASSETS.items():
        assert (ROOT / "docs" / "brand" / name).read_text(encoding="utf-8") == render() + "\n", f"docs/brand/{name} is stale: brand.write_assets"
    bundle = json.loads(brand.bundle_json())
    assert bundle["logo"] == brand.logo_body()
    for key in brand.AGENTS:
        assert bundle["agents"][key]["svg"] == brand.portrait_body(key)
        assert bundle["agents"][key]["svg"] in brand.party_svg()
    assert set(bundle["agents"]) == set(brand.AGENTS)
    assert set(brand.ASSETS) == {"logo.svg", "mark.svg", "party.svg", "mark-animated.svg", "flow.svg",
                                 "lockup-animated-light.svg", "lockup-animated-dark.svg",
                                 "party-animated-light.svg", "party-animated-dark.svg"}
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for used in ("docs/brand/lockup-animated-light.svg", "docs/brand/lockup-animated-dark.svg",
                 "docs/brand/party-animated-light.svg", "docs/brand/party-animated-dark.svg", "docs/brand/flow.svg"):
        assert used in readme, used
    assert not (ROOT / "docs" / "brand" / "lockup-animated.svg").exists() and not (ROOT / "docs" / "brand" / "party-animated.svg").exists()
    assert "```mermaid" not in readme and "# Parallax\n" not in readme  # the lockup is the heading
    social = (ROOT / "docs" / "brand" / "social.html").read_text(encoding="utf-8")
    assert brand.logo_body() in social and "Agents do the work. You make the calls." in social


def test_the_brand_is_plain_words_and_its_own():
    """No em dashes, and every proper noun in the brand module is one of the agents or Parallax."""
    text = Path(brand.__file__).read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2014" not in (ROOT / "docs" / "brand" / "party.svg").read_text()
    ours = {"Focus", "Reticle", "Maker", "Second", "Eye", "Field", "Parallax", "Scope"}
    prose = " ".join(re.findall(r'"""(.*?)"""', text, re.S)) + " ".join(re.findall(r"(?:^|\s)#\s+(.*)", text))  # comments, not #hex colors
    capitalised = {w for w in re.findall(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]+)\b", prose, re.M)}
    common = {"The", "A", "An", "One", "Names", "Portraits", "Motion", "Any", "Both", "Claude", "Code", "Both", "Focus"}
    assert capitalised - ours - common == set(), capitalised - ours - common


def test_stages_follow_the_ledger():
    def e(kind, **data):
        return {"kind": kind, "data": data, "ts": "2026-01-01T00:00:00+00:00", "reason": ""}
    drafting = [e("pilot.started", task="t")]
    assert live.stages(drafting, waiting=False) == [{"agent": "focus", "state": "working"}, {"agent": "maker", "state": "waiting"},
                                                    {"agent": "second_eye", "state": "waiting"}]
    building = drafting + [e("draft.recorded", doc="intent"), e("draft.recorded", doc="plan"), e("gate.approved"), e("maker.started", stage="build")]
    assert [s["state"] for s in live.stages(building, waiting=False)] == ["done", "working", "waiting"]
    checking = building + [e("build.finished", status="built"), e("check.started"), e("tests.recorded", passed=1, total=1, exit=0)]
    assert [s["state"] for s in live.stages(checking, waiting=False)] == ["done", "done", "working"]
    ready = checking + [e("verdict.recorded", stage="check"), e("check.finished", status="ready")]
    assert [s["state"] for s in live.stages(ready, waiting=True)] == ["done", "done", "done"]  # waiting on you: nothing moves
    with_field = building + [e("build.finished", status="built"), e("check.started"), e("uitest.started")]
    assert [(s["agent"], s["state"]) for s in live.stages(with_field, waiting=False)][-1] == ("field", "working")
    assert live.agent_of("Maker reworking (1 of 3)") == "maker" and live.agent_of("running the plan's tests") is None
    # Reticle, between Focus and Maker, on a task it ran for
    approved = drafting + [e("draft.recorded", doc="intent"), e("draft.recorded", doc="plan"), e("gate.approved")]
    writing = approved + [e("reticle.started", task="t")]
    assert live.doing(writing)[0] == "Reticle writing tests of what you asked"
    assert [(s["agent"], s["state"]) for s in live.stages(writing, waiting=False)][:3] == [
        ("focus", "done"), ("reticle", "working"), ("maker", "waiting")]
    built = writing + [e("reticle.recorded", task="t"), e("maker.started", stage="build")]
    assert [(s["agent"], s["state"]) for s in live.stages(built, waiting=False)][1:3] == [("reticle", "done"), ("maker", "working")]
    assert "reticle" not in [s["agent"] for s in live.stages(building, waiting=False)]  # off, or not run: not shown
    assert not re.search(r"drafter|checker|tester", " ".join(a["name"] for a in brand.AGENTS.values()), re.I)


def test_the_lockup_is_the_animated_mark_with_the_uis_wordmark():
    svg = brand.lockup_svg()
    assert brand.logo_body() in svg and brand.DRIFT in svg  # the P and its drift, exactly as mark-animated.svg
    assert "@media (prefers-reduced-motion: reduce){.l1,.l2{animation:none}}" in svg
    assert 'font-family:ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;font-weight:600' in svg  # app.css --mono, .word
    assert "fill:#1c1c1a" in svg and "fill:#ecebe6" in brand.lockup_svg("dark")  # app.css --ink, one per file
    assert 'aria-label="Parallax"' in svg
    for name in ("mark.svg", "mark-animated.svg", "logo.svg"):
        assert "parallax</text>" not in (ROOT / "docs" / "brand" / name).read_text() or name == "logo.svg"


def test_the_party_takes_turns_and_only_one_agent_moves_at_a_time():
    svg = brand.party_animated_svg()
    assert "@media (prefers-reduced-motion: reduce){.bust,.glow{animation:none}}" in svg
    windows = brand.party_windows()
    n_agents = len(brand.AGENTS)
    assert len(windows) == n_agents and all(a[1] <= b[0] for a, b in zip(windows, windows[1:]))  # in order, never overlapping
    total = n_agents * brand.TURN + brand.PAUSE
    # from the keyframes themselves: every moment at which an agent is not at rest belongs to one agent only
    moving: dict[int, list[float]] = {}
    for n in range(n_agents):
        for prop, rest in (("bob", "translateY(0)"), ("glow", "1")):
            block = re.search(rf"@keyframes {prop}{n}\{{(.*?)\}}(?=@keyframes|\.a|$)", svg).group(1)
            frames = re.findall(r"([\d.]+)%\{[a-z]+:([^}]+)\}", block)
            moving.setdefault(n, [])
            for pct, value in frames:
                if value != rest:
                    moving[n].append(float(pct) / 100 * total)
    for a in range(n_agents):
        for b in range(a + 1, n_agents):
            assert not (set(round(x, 3) for x in moving[a]) & set(round(x, 3) for x in moving[b])), (a, b)
        lo, hi = windows[a]
        eps = total / 100 * 0.001  # keyframes are written to three decimals of a percent
        assert all(lo - eps <= x < hi - eps for x in moving[a]), f"agent {a} moves outside its turn"
        assert moving[a], f"agent {a} never moves"
    assert svg.count('class="agent a') == n_agents and all(f".a{n} .bust{{animation:bob{n}" in svg for n in range(n_agents))
    assert "steps(1,end)" in svg
    assert brand.party_svg() != svg and all(brand.portrait_body(k) in svg for k in brand.AGENTS)


def test_images_on_the_page_never_pick_their_color_from_the_os():
    """An image's prefers-color-scheme follows the viewer's OS, not their GitHub theme, so a wordmark
    or a name could land dark on a dark page. Anything on the page background ships as a light and a
    dark file, chosen by a <picture>; the flow diagram sits on its own tile with fixed colors."""
    for name in ("lockup-animated-light.svg", "lockup-animated-dark.svg", "party-animated-light.svg",
                 "party-animated-dark.svg", "party.svg", "flow.svg"):
        assert "prefers-color-scheme" not in (ROOT / "docs" / "brand" / name).read_text(encoding="utf-8"), name
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for light, dark in (("lockup-animated-light.svg", "lockup-animated-dark.svg"), ("party-animated-light.svg", "party-animated-dark.svg")):
        block = re.search(r"<picture>(.*?)</picture>", readme[max(0, readme.index(dark) - 200):], re.S).group(1)
        assert f'<source media="(prefers-color-scheme: dark)" srcset="docs/brand/{dark}">' in block
        assert f'<img src="docs/brand/{light}"' in block
    assert "#1c1c1a" in brand.lockup_svg("light") and "#ecebe6" in brand.lockup_svg("dark")
    assert brand.NAME_INK["light"] in brand.party_animated_svg("light") and brand.NAME_INK["dark"] in brand.party_animated_svg("dark")


def test_reticles_portrait_is_a_clear_gap_until_it_is_drawn():
    """The portrait is still to come: the tile with a dashed outline, no halo and no pixels, everywhere
    the others appear. Filling its rows and colors and deleting "placeholder" is all it takes."""
    body = brand.portrait_body("reticle")
    assert 'class="gap"' in body and 'stroke-dasharray="1 1"' in body and "<circle" not in body
    assert brand.pixels("reticle") == [] and brand.PORTRAITS["reticle"]["placeholder"] is True
    assert brand.bundle()["agents"]["reticle"]["svg"] == body  # the UI's
    for name in ("party.svg", "party-animated-light.svg", "flow.svg"):
        assert body in (ROOT / "docs" / "brand" / name).read_text(), name
    assert "Reticle" in (ROOT / "docs" / "brand" / "party.svg").read_text()
