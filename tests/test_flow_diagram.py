"""docs/brand/flow.svg, rendered in Chromium: every text fits its box with room, nothing overlaps."""
import os
import re
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="needs Playwright: scripts/test.sh")
from playwright.sync_api import sync_playwright  # noqa: E402

from parallax import brand  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PADDING = 4  # px of room a text must keep inside its box, on every side

MEASURE = """() => {
  const svg = document.querySelector('svg');
  const box = e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom}; };
  const out = {tile: box(svg.querySelector('.tile')), steps: [], notes: [], arrows: [], other: []};
  for (const g of svg.querySelectorAll('.step')) {
    out.steps.push({step: g.dataset.step, box: box(g.querySelector('.box')), label: box(g.querySelector('.label')),
                    text: g.querySelector('.label').textContent,
                    portraits: [...g.querySelectorAll('.portrait')].map(p => ({agent: p.dataset.agent, box: box(p)}))});
  }
  for (const n of svg.querySelectorAll('.note')) out.notes.push({step: n.dataset.step, box: box(n), text: n.textContent});
  for (const a of svg.querySelectorAll('.arrow')) out.arrows.push(box(a));
  for (const c of ['.bracket', '.bracket-label', '.ledger']) out.other.push({name: c, box: box(svg.querySelector(c))});
  const ls = [...svg.querySelectorAll('.legend')].map(box);
  out.legend = {l: Math.min(...ls.map(b => b.l)), t: Math.min(...ls.map(b => b.t)), r: Math.max(...ls.map(b => b.r)), b: Math.max(...ls.map(b => b.b))};
  for (const g of svg.querySelectorAll('.legend')) out.other.push({name: 'legend ' + g.dataset.owner, box: box(g)});
  return out; }"""


def inside(inner, outer, pad=0):
    return inner["l"] >= outer["l"] + pad and inner["r"] <= outer["r"] - pad and inner["t"] >= outer["t"] + pad and inner["b"] <= outer["b"] - pad


def overlap(a, b, slack=1.0):
    return a["l"] < b["r"] - slack and b["l"] < a["r"] - slack and a["t"] < b["b"] - slack and b["t"] < a["b"] - slack


@pytest.fixture(scope="module")
def measured():
    svg = (ROOT / "docs" / "brand" / "flow.svg").read_text(encoding="utf-8")
    assert svg == brand.flow_svg() + "\n", "docs/brand/flow.svg is stale: brand.write_assets"
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=os.environ.get("PARALLAX_BROWSER") or None)
        except Exception as err:
            pytest.skip(f"Chromium can't start: {str(err).splitlines()[0]}")
        pg = b.new_page(viewport={"width": 800, "height": 1000})
        pg.set_content(f'<html><body style="margin:0;padding:24px;background:#fff">{svg}</body></html>')
        data = pg.evaluate(MEASURE)
        b.close()
    return data


def test_every_text_fits_its_box_with_padding(measured):
    assert len(measured["steps"]) == len(brand.FLOW_STEPS)
    assert inside(measured["legend"], measured["tile"], PADDING)
    for s in measured["steps"]:
        assert inside(s["label"], s["box"], PADDING), f"step {s['step']} spills: {s['text']!r} {s['label']} in {s['box']}"
        for p in s["portraits"]:
            assert inside(p["box"], s["box"], PADDING), f"portrait {p['agent']} spills its box"
            assert not overlap(p["box"], s["label"]), f"portrait {p['agent']} covers the text"
    for n in measured["notes"]:
        assert inside(n["box"], measured["tile"], PADDING), f"note spills the diagram: {n['text']!r}"
    for o in measured["other"]:
        assert inside(o["box"], measured["tile"], PADDING), f"{o['name']} spills the diagram"


def test_nothing_overlaps_except_a_portrait_on_its_own_box(measured):
    items = []
    for s in measured["steps"]:
        items.append((f"box {s['step']}", s["box"], s["step"]))
        for p in s["portraits"]:
            items.append((f"portrait {p['agent']}", p["box"], s["step"]))
    for n in measured["notes"]:
        items.append((f"note {n['step']}", n["box"], None))
    for i, a in enumerate(measured["arrows"]):
        items.append((f"arrow {i}", a, None))
    for o in measured["other"]:
        items.append((o["name"], o["box"], None))
    for i, (name_a, a, own_a) in enumerate(items):
        for name_b, b, own_b in items[i + 1:]:
            same_box = own_a is not None and own_a == own_b and {name_a.split()[0], name_b.split()[0]} == {"box", "portrait"}
            if same_box:
                continue
            assert not overlap(a, b), f"{name_a} overlaps {name_b}: {a} vs {b}"


def test_notes_stay_on_at_most_two_lines_and_nothing_is_rotated():
    svg = brand.flow_svg()
    assert "rotate(" not in svg, "a label is rotated"
    for m in re.finditer(r'<text class="note"[^>]*>(.*?)</text>', svg):
        lines = m.group(1).count("<tspan")
        assert lines <= 2, f"a side note wraps past two lines: {re.sub('<[^>]+>', '', m.group(1))!r}"
    assert 'class="bracket-label"' in svg and 'text-anchor="end"' in svg


def test_the_lockup_word_fits_the_image(measured_lockup):
    word, image = measured_lockup["word"], measured_lockup["image"]
    assert inside(word, image, 1), f"the word spills the lockup: {word} in {image}"
    assert not overlap(word, measured_lockup["p"]), "the word runs into the P"
    assert abs((word["t"] + word["b"]) / 2 - (image["t"] + image["b"]) / 2) < 4, "the word isn't centred on the P"


@pytest.fixture(scope="module")
def measured_lockup():
    svg = (ROOT / "docs" / "brand" / "lockup-animated-light.svg").read_text(encoding="utf-8")
    assert svg == brand.lockup_svg("light") + "\n"
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=os.environ.get("PARALLAX_BROWSER") or None)
        except Exception as err:
            pytest.skip(f"Chromium can't start: {str(err).splitlines()[0]}")
        pg = b.new_page(viewport={"width": 800, "height": 300})
        pg.set_content(f'<html><body style="margin:0;padding:24px;background:#fff">{svg}</body></html>')
        data = pg.evaluate("""() => { const svg = document.querySelector('svg');
            const box = e => { const r = e.getBoundingClientRect(); return {l: r.left, t: r.top, r: r.right, b: r.bottom}; };
            return {image: box(svg), word: box(svg.querySelector('.word')), p: box(svg.querySelector('.l1'))}; }""")
        b.close()
    return data


def test_every_step_has_an_owner_and_the_legend_shows_the_three():
    svg = brand.flow_svg()
    owners = re.findall(r'<g class="step" data-step="\d+" data-owner="(\w+)">', svg)
    assert len(owners) == len(brand.FLOW_STEPS) and set(owners) <= set(brand.OWNERS)
    assert {o for _, _, _, o in brand.FLOW_STEPS} == {"you", "agents", "parallax"}
    legend = re.findall(r'<g class="legend" data-owner="(\w+)">.*?<text[^>]*>([^<]+)</text>', svg)
    assert legend == [("you", "You"), ("agents", "Agents"), ("parallax", "Parallax (automatic)")]
    for key, (label, fill, edge) in brand.OWNERS.items():  # each step's box carries its owner's fill
        for m in re.finditer(rf'data-owner="{key}"><rect class="box"[^>]*fill="([^"]+)"', svg):
            assert m.group(1) == fill
    assert "tamper-evident log" in svg


def test_notes_sit_beside_their_boxes():
    """A side note is vertically centered on the box it belongs to, in the column to the right."""
    svg = brand.flow_svg()
    boxes = {m.group(1): (float(m.group(2)), float(m.group(3)), float(m.group(4)), float(m.group(5))) for m in
             re.finditer(r'data-step="(\d+)"[^>]*><rect class="box" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)}
    assert len(boxes) == len(brand.FLOW_STEPS)
    for m in re.finditer(r'<text class="note" data-step="(\d+)" x="([\d.]+)" y="([\d.]+)"', svg):
        step, x, y = m.group(1), float(m.group(2)), float(m.group(3))
        bx, by, bw, bh = boxes[step]
        assert x > bx + bw, "the note is right of its box"
        lines = svg[m.end():].split("</text>", 1)[0].count("<tspan")
        centre = y + (lines - 1) * 13 * 1.3 / 2
        assert abs(centre - (by + bh / 2)) < 1.0, f"note {step} isn't centred on its box"


def _lab(color: str) -> tuple[float, float, float]:
    """sRGB to CIE Lab, so color distance is the perceived kind (CIE76 delta E)."""
    def lin(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = [lin(int(color[i:i + 2], 16) / 255) for i in (1, 3, 5)]
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(v):
        return v ** (1 / 3) if v > 0.008856 else 7.787 * v + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _distance(a: str, b: str) -> float:
    return sum((p - q) ** 2 for p, q in zip(_lab(a), _lab(b))) ** 0.5


def _contrast(a: str, b: str) -> float:
    la, lb = (((_lab(c)[0] + 16) / 116) ** 3 for c in (a, b))  # relative luminance, from L*
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_each_owner_fill_stands_apart_from_the_tile_and_the_others_and_carries_its_text():
    """A box the same shade as the tile disappears (the Parallax steps did, at #3f4775). Every owner's
    fill is clearly apart from the tile and from each other, in perceived color, and reads AA."""
    fills = {key: fill for key, (_, fill, _) in brand.OWNERS.items()}
    for key, fill in fills.items():
        assert _distance(fill, brand.TILE) >= 15, f"{key} fill {fill} is too close to the tile {brand.TILE}"
        assert _contrast(brand.FLOW_COLORS["ink"], fill) >= 4.5, f"{key} fill {fill} doesn't carry the text at AA"
    keys = list(fills)
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            assert _distance(fills[a], fills[b]) >= 15, f"{a} and {b} fills are too close: {fills[a]} vs {fills[b]}"
