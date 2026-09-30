"""docs/brand/flow.svg, rendered in Chromium: every text fits its box with room, nothing overlaps."""
import os
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


def test_notes_sit_beside_their_boxes():
    """A side note is vertically centered on the box it belongs to, in the column to the right."""
    svg = brand.flow_svg()
    import re
    boxes = {m.group(1): (float(m.group(2)), float(m.group(3))) for m in
             re.finditer(r'data-step="(\d+)"><rect class="box" x="([\d.]+)" y="([\d.]+)" width="([\d.]+)" height="([\d.]+)"', svg)}
    for m in re.finditer(r'<text class="note" data-step="(\d+)" x="([\d.]+)" y="([\d.]+)"', svg):
        step, x, y = m.group(1), float(m.group(2)), float(m.group(3))
        bx, by = boxes[step]
        assert x > bx + 300, "the note is right of its box"
        lines = svg[m.end():].split("</text>", 1)[0].count("<tspan")
        centre = y + (lines - 1) * 11 * 1.3 / 2
        assert abs(centre - (by + 21)) < 1.0, f"note {step} isn't centred on its box"
