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


ASSETS = {"logo.svg": lambda: logo_svg(word=True), "mark.svg": lambda: logo_svg(), "party.svg": party_svg,
          "mark-animated.svg": lambda: logo_svg(animated=True)}


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
