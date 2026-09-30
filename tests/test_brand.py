"""The agents' names and faces, and the logo: one data source for the UI and docs/brand/."""
import json
import re
from pathlib import Path

from parallax import brand, live

ROOT = Path(__file__).resolve().parents[1]


def test_the_four_agents_have_names_and_roles_and_nothing_else_is_named():
    assert [a["name"] for a in brand.AGENTS.values()] == ["Focus", "Maker", "Second Eye", "Field"]
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
    social = (ROOT / "docs" / "brand" / "social.html").read_text(encoding="utf-8")
    assert brand.logo_body() in social and "Agents do the work. You make the calls." in social


def test_the_brand_is_plain_words_and_its_own():
    """No em dashes, and every proper noun in the brand module is one of the four agents or Parallax."""
    text = Path(brand.__file__).read_text(encoding="utf-8")
    assert "\u2014" not in text and "\u2014" not in (ROOT / "docs" / "brand" / "party.svg").read_text()
    ours = {"Focus", "Maker", "Second", "Eye", "Field", "Parallax", "Scope"}
    prose = " ".join(re.findall(r'"""(.*?)"""', text, re.S)) + " ".join(re.findall(r"#\s*(.*)", text))
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
    assert not re.search(r"drafter|checker|tester", " ".join(a["name"] for a in brand.AGENTS.values()), re.I)
