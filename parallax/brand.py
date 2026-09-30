"""The agents' names and faces, and the logo, as data. One source for the UI and for docs/brand/.

Names, with the role shown beside each the first time it appears on a screen or page:
Focus drafts the intent and plan; Maker builds in the sandbox; Second Eye is the blind checker,
which sees only the result, never the making; Field is the UI tester. Portraits are 16 by 16
pixel busts drawn as one SVG rect per pixel. Motion means status: a portrait moves only while
its agent is working (a one-pixel bob, glow letters pulsing), hops once when its stage finishes,
and is still otherwise; with prefers-reduced-motion nothing moves. The mapping is `motion()`.
"""
from __future__ import annotations

import json
from pathlib import Path

TILE = "#313859"
VIOLET, CYAN = "#a47cf0", "#5ef2ff"
BASE = {"k": "#12131f", "s": "#f0c39a", "w": "#ffffff"}  # outline, skin, eye glint
LIGHT, DARK = 0.22, 0.25  # side light: lit from the left, shaded on the right

AGENTS = {  # role: shown beside the name the first time it appears; short: under the portrait on a card
    "focus": {"name": "Focus", "role": "drafts the intent and plan", "short": "intent and plan"},
    "maker": {"name": "Maker", "role": "builds in the sandbox", "short": "builds"},
    "second_eye": {"name": "Second Eye", "role": "the blind checker; sees only the result, never the making", "short": "blind checker"},
    "field": {"name": "Field", "role": "the UI tester", "short": "UI tester"},
}

PORTRAITS = {
    "focus": {"halo": "#5ef2ff", "glow": "rxo",
              "colors": {"g": "#3d5a8a", "G": "#2a3f63", "r": "#5ef2ff", "x": "#5ef2ff", "o": "#5ef2ff"},
              "rows": ["................", ".....kkkkkk.....", "....kggrrggk....", "...kggggggggk...",
                       "..kggggggggggk..", "..kgssssssssgk..", "..kgsoosskwsgk..", "..kgsoosssssgk..",
                       "..kgssskksssgk..", "...kgssssssgk...", "...kggssssggk...", "..kggggxxggggk..",
                       ".kggggxxxxggggk.", "kGGGGGgxxgGGGGGk", "kGGGGGGggGGGGGGk", "kGGGGGGGGGGGGGGk"]},
    "maker": {"halo": "#ff7a3d", "glow": "ox",
              "colors": {"h": "#d06a2a", "o": "#ffb347", "a": "#7a4b32", "c": "#3b2a22", "m": "#9aa4b0", "x": "#ff7a3d"},
              "rows": ["................", ".....kkkkkk.....", "....khhhhhhk....", "...khhhhhhhhk...",
                       "...khkkkkkkhk...", "...kkwokkwokk...", "...kkookkookk...", "...kssssssssk...",
                       "...kssskksssk...", "....kssssssk....", ".....kssssk.....", "..kkaaaaaaaakk..",
                       ".kaaacaaaacaaak.", "kmmmacaaaacaaaak", "kmxmacaaaacaaaak", "kmmmaaaaaaaaaaak"]},
    "second_eye": {"halo": "#a47cf0", "glow": "xv",
                   "colors": {"v": "#a47cf0", "V": "#4b3b78", "m": "#b9c0cc", "M": "#6f7788", "x": "#c9a8ff", "y": "#d9a93a"},
                   "rows": [".......kk.......", "......kvvk......", "....kkmmmmkk....", "...kmmmmmmmmk...",
                            "..kmmmmmmmmmmk..", "..kmMMMMMMMMmk..", "..kmkkkxxkkkmk..", "..kmMMMMMMMMmk..",
                            "..kmmmmmmmmmmk..", "...kmmMMMMmmk...", "....kmmmmmmk....", "..kkvvvvvvvvkk..",
                            ".kvvvvyyyyvvvvk.", "kvvvvvyxxyvvvvvk", "kVVVVVyyyyVVVVVk", "kVVVVVVVVVVVVVVk"]},
    "field": {"halo": "#f2c94c", "glow": "lx",
              "colors": {"l": "#f2c94c", "t": "#35607d", "T": "#244459", "x": "#5ef2ff", "r": "#c0392b"},
              "rows": ["...........kl...", "..........k.....", ".....kkkkkk.....", "....kttttttk....",
                       "...kttttttttk...", "...kTTTTTTTTk...", "...kssssssssk...", "...kxxxxxxxxk...",
                       "...kxxxxxxxxk...", "...kssssssssk...", "....ksskkssk....", "...krrrrrrrrk...",
                       "..krrrrrrrrrrk..", ".kttttttrrttttk.", "kttttttrrttttttk", "kttttttttttttttk"]},
}

# the letter P, 8 wide by 12 tall, drawn twice on a 14 by 16 grid: violet at (2, 3), cyan at (4, 1)
LOGO_ROWS = ["kkkkkk..", "kkkkkkk.", "kk...kkk", "kk....kk", "kk....kk", "kk...kkk",
             "kkkkkkk.", "kkkkkk..", "kk......", "kk......", "kk......", "kk......"]
LOGO_LAYERS = ((VIOLET, 2, 3, 1.0), (CYAN, 4, 1, 0.85))
LOGO_W, LOGO_H = 14, 16


def label(key: str, with_role: bool = False) -> str:
    """"Second Eye", or with its role the first time it appears: "Second Eye (the blind checker; ...)"."""
    a = AGENTS[key]
    return f"{a['name']} ({a['role']})" if with_role else a["name"]


def motion(prev: str | None, now: str) -> str:
    """What a portrait does: "working" animates, "hop" once when its stage just finished, else "still".

    now is the agent's state on this task: working, done, or waiting (idle, or the task waits on you)."""
    if now == "working":
        return "working"
    if prev == "working":
        return "hop"
    return "still"


def _mix(color: str, toward: str, amount: float) -> str:
    a = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(toward[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * amount):02x}" for x, y in zip(a, b))


def pixels(key: str) -> list[tuple[int, int, str, bool]]:
    """(x, y, fill, glow) for every filled pixel, side light applied.

    Any non-outline, non-glow, non-glint pixel whose left neighbor is the outline (or that sits in
    column 0) is lightened toward white; one whose right neighbor is the outline (or in column 15)
    is darkened toward black. Both can apply."""
    p = PORTRAITS[key]
    colors = {**BASE, **p["colors"]}
    out = []
    for y, row in enumerate(p["rows"]):
        assert len(row) == 16, (key, y)
        for x, c in enumerate(row):
            if c == ".":
                continue
            fill = colors[c]
            glow = c in p["glow"]
            if c not in ("k", "w") and not glow:
                if x == 0 or row[x - 1] == "k":
                    fill = _mix(fill, "#ffffff", LIGHT)
                if x == 15 or row[x + 1] == "k":
                    fill = _mix(fill, "#000000", DARK)
            out.append((x, y, fill, glow))
    return out


def portrait_body(key: str) -> str:
    """The inner SVG of one portrait, at 16 by 16 units: tile, halo, then one rect per pixel."""
    parts = [f'<rect width="16" height="16" rx="2" fill="{TILE}"/>',
             f'<circle cx="8" cy="8.5" r="7.5" fill="{PORTRAITS[key]["halo"]}" opacity="0.16"/>']
    for x, y, fill, glow in pixels(key):
        cls = ' class="glow"' if glow else ""
        parts.append(f'<rect x="{x}" y="{y}" width="1" height="1" fill="{fill}"{cls}/>')
    return "".join(parts)


def portrait_svg(key: str, size: int = 64) -> str:
    a = AGENTS[key]
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="{size}" height="{size}" '
            f'shape-rendering="crispEdges" role="img" aria-label="{a["name"]}, {a["role"]}">'
            f'{portrait_body(key)}</svg>')


def party_svg() -> str:
    """The four in a row, names under them, for the README."""
    gap, size = 3, 16
    width = len(AGENTS) * size + (len(AGENTS) - 1) * gap
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 22" width="{width * 6}" height="{22 * 6}" '
             'shape-rendering="crispEdges" role="img" aria-label="Focus, Maker, Second Eye and Field">',
             '<style>text{fill:#5f6480}@media (prefers-color-scheme: dark){text{fill:#a8adc4}}</style>']
    for n, key in enumerate(AGENTS):
        x = n * (size + gap)
        parts.append(f'<g transform="translate({x} 0)">{portrait_body(key)}</g>')
        parts.append(f'<text x="{x + 8}" y="20.5" text-anchor="middle" font-family="ui-monospace, Menlo, Consolas, monospace" '
                     f'font-size="2.6">{AGENTS[key]["name"]}</text>')
    parts.append("</svg>")
    return "".join(parts)


def logo_body() -> str:
    """Two layers of the P, each a group so the UI can move them apart on hover."""
    out = []
    for n, (color, dx, dy, opacity) in enumerate(LOGO_LAYERS, start=1):
        rects = "".join(f'<rect x="{x + dx}" y="{y + dy}" width="1" height="1"/>'
                        for y, row in enumerate(LOGO_ROWS) for x, c in enumerate(row) if c == "k")
        out.append(f'<g class="layer l{n}" fill="{color}" opacity="{opacity}">{rects}</g>')
    return "".join(out)


DRIFT = ('<style>.l1,.l2{animation:7s steps(2) infinite}.l1{animation-name:drift1}.l2{animation-name:drift2}'
         '@keyframes drift1{0%,86%{transform:translate(0,0)}93%{transform:translate(-1px,1px)}100%{transform:translate(0,0)}}'
         '@keyframes drift2{0%,86%{transform:translate(0,0)}93%{transform:translate(1px,-1px)}100%{transform:translate(0,0)}}'
         '@media (prefers-reduced-motion: reduce){.l1,.l2{animation:none}}</style>')


def logo_svg(word: bool = False, size: int = 32, animated: bool = False) -> str:
    """The pixel P; with word, the name beside it in the monospace font. animated: the two layers drift
    one pixel further apart and back every few seconds, in two steps, by CSS inside the file; still
    under prefers-reduced-motion. For the README header only; logo.svg and mark.svg stay static."""
    w = LOGO_W + (4 + 48 if word else 0)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {LOGO_H}" height="{size}" '
             f'width="{round(size * w / LOGO_H)}" shape-rendering="crispEdges" role="img" aria-label="parallax">',
             DRIFT if animated else "", logo_body()]
    if word:
        parts.append(f'<text x="{LOGO_W + 4}" y="12.2" font-family="ui-monospace, Menlo, Consolas, monospace" '
                     'font-size="10.5" fill="#7c81a1">parallax</text>')
    parts.append("</svg>")
    return "".join(parts)


def bundle() -> dict:
    """What the UI fetches once: the logo and every portrait, rendered here so the page holds no drawing rules."""
    return {"logo": logo_body(), "agents": {key: {**AGENTS[key], "halo": PORTRAITS[key]["halo"], "svg": portrait_body(key)}
                                            for key in AGENTS}}


# the flow diagram: one column of equal boxes, arrows down the middle, notes to the right ----------------

FLOW_FONT = '-apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif'
FLOW_STEPS = [  # (text, portraits, side note)
    ("You describe the work", (), ""),
    ("Intake: the UI's box, or parallax do", (), ""),
    ("Focus drafts the intent and plan", ("focus",), "misfit: redraft, up to 2"),
    ("Plan checked against the intent, by code", (), ""),
    ("Launch rule", (), "over the limit, or crosses the boundary: asks you"),
    ("Maker builds in the sandbox", ("maker",), ""),
    ("Check: your tests, Second Eye, Field", ("second_eye", "field"), "findings: rework, up to 3"),
    ("One card: Ready, or one decision", (), ""),
    ("Accept commits exactly the reviewed tree", (), ""),
    ("You merge", (), ""),
]
WITHOUT_YOU = (2, 6)  # the steps that run without you, first and last, for the bracket
FLOW_COLORS = {"box": "#3f4775", "edge": "#5a63a0", "ink": "#f2f3fa", "muted": "#bfc4dd", "line": "#9aa1c9"}
_NARROW, _WIDE = set("iljtfr.,:;' !"), set("mwMW")


def _text_width(text: str, size: float) -> float:
    """A generous estimate for a system sans-serif: the diagram test measures the real thing in a browser."""
    em = 0.0
    for c in text:
        em += 0.34 if c in _NARROW else 0.88 if c in _WIDE else 0.72 if c.isupper() else 0.6
    return em * size


def _wrap(text: str, size: float, width: float) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if line and _text_width(trial, size) > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line] if line else lines


def flow_svg() -> str:
    """How a task moves, drawn: sized from the text, on the brand tile, portraits from the same data."""
    W, size, note_size = 600, 13.5, 11
    pad_x, box_h, gap, pic = 16, 42, 26, 24
    widest = max(_text_width(text, size) + 2 * pad_x + len(pics) * (pic + 8) for text, pics, _ in FLOW_STEPS)
    box_w = int(widest + 0.5)
    col_x = (W - box_w) // 2
    note_x = col_x + box_w + 14
    note_w = W - note_x - 12
    top = 26
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {{H}}" width="{W}" height="{{H}}" '
             f'role="img" aria-label="How a task moves: from your words through Focus, the plan check, the launch rule, '
             f'Maker, the check and one card to your accept and your merge, every step on the ledger" '
             f'font-family=\'{FLOW_FONT}\' font-size="{size}">',
             f'<rect class="tile" width="{W}" height="{{H}}" rx="14" fill="{TILE}"/>']
    y = top
    centers = []
    for i, (text, pics, note) in enumerate(FLOW_STEPS):
        cy = y + box_h / 2
        centers.append(cy)
        parts.append(f'<g class="step" data-step="{i}">'
                     f'<rect class="box" x="{col_x}" y="{y}" width="{box_w}" height="{box_h}" rx="8" '
                     f'fill="{FLOW_COLORS["box"]}" stroke="{FLOW_COLORS["edge"]}" stroke-width="1"/>')
        x = col_x + pad_x
        for key in pics:
            s = pic / 16
            parts.append(f'<g class="portrait" data-agent="{key}" transform="translate({x} {cy - pic / 2}) scale({s:.4f})">'
                         f'{portrait_body(key)}</g>')
            x += pic + 8
        parts.append(f'<text class="label" x="{x}" y="{cy:.1f}" dominant-baseline="central" fill="{FLOW_COLORS["ink"]}">{text}</text></g>')
        if note:
            lines = _wrap(note, note_size, note_w)
            lh = note_size * 1.3
            ny = cy - (len(lines) - 1) * lh / 2
            parts.append(f'<text class="note" data-step="{i}" x="{note_x}" y="{ny:.1f}" dominant-baseline="central" '
                         f'font-size="{note_size}" fill="{FLOW_COLORS["muted"]}">'
                         + "".join(f'<tspan x="{note_x}" dy="{0 if n == 0 else lh:.1f}">{line}</tspan>' for n, line in enumerate(lines))
                         + "</text>")
        if i < len(FLOW_STEPS) - 1:  # the arrow to the next box: a line and a small head, down the middle
            ax, y1, y2 = W / 2, y + box_h + 1, y + box_h + gap - 1
            parts.append(f'<g class="arrow"><line x1="{ax}" y1="{y1}" x2="{ax}" y2="{y2 - 5}" stroke="{FLOW_COLORS["line"]}" stroke-width="1.5"/>'
                         f'<path d="M{ax - 4} {y2 - 6} L{ax} {y2} L{ax + 4} {y2 - 6} Z" fill="{FLOW_COLORS["line"]}"/></g>')
        y += box_h + gap
    # the bracket on the left for the steps that run without you, with a short label along it
    first, last = WITHOUT_YOU
    by1, by2 = centers[first] - box_h / 2, centers[last] + box_h / 2
    bx = col_x - 14
    parts.append(f'<path class="bracket" d="M{bx + 6} {by1} H{bx} V{by2} H{bx + 6}" fill="none" stroke="{FLOW_COLORS["line"]}" stroke-width="1.5"/>')
    lx, ly = bx - 8, (by1 + by2) / 2
    parts.append(f'<text class="bracket-label" x="{lx}" y="{ly:.1f}" transform="rotate(-90 {lx} {ly:.1f})" text-anchor="middle" '
                 f'dominant-baseline="central" font-size="{note_size}" fill="{FLOW_COLORS["muted"]}">runs without you</text>')
    ly2 = y - gap + 24
    parts.append(f'<text class="ledger" x="{W / 2}" y="{ly2}" text-anchor="middle" dominant-baseline="central" font-size="{note_size + 1}" '
                 f'fill="{FLOW_COLORS["muted"]}">Every step is written to the hash-chained ledger.</text>')
    H = int(ly2 + 22)
    parts.append("</svg>")
    return "".join(parts).replace("{H}", str(H))


ASSETS = {"logo.svg": lambda: logo_svg(word=True), "mark.svg": lambda: logo_svg(), "party.svg": party_svg,
          "mark-animated.svg": lambda: logo_svg(animated=True), "flow.svg": flow_svg}


def write_assets(folder: Path) -> list[Path]:
    """docs/brand/: the static files, from the same data the UI serves. A test keeps them equal."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for name, render in ASSETS.items():
        path = folder / name
        path.write_text(render() + "\n", encoding="utf-8")
        out.append(path)
    return out


def bundle_json() -> bytes:
    return json.dumps(bundle(), separators=(",", ":")).encode("utf-8")
