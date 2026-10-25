# Parallax brand

Every SVG here is drawn from code by `parallax/brand.py`, from the same pixel data the UI serves as `/brand.json`. To change one, edit the module, then write the files again with `brand.write_assets(Path("docs/brand"))`. `tests/test_brand.py` fails if a file and the module disagree.

## The mark

A pixel P drawn twice, offset: a violet layer and a cyan one, the parallax. In the UI it drifts apart for an instant every seven seconds and settles again; viewers who ask for reduced motion see it still.

| File | What it is | Where it is used |
|---|---|---|
| [mark.svg](mark.svg) | The P alone, still. | The UI's favicon, served as `/favicon.svg` from the same rendering. |
| [mark-animated.svg](mark-animated.svg) | The P with its drift. | The UI's header logo, inlined from `/brand.json`. |
| [logo.svg](logo.svg) | The P with the word PARALLAX beside it, still. | Nothing in the repo today; kept for use outside it. |

## The lockup

The animated P and the word PARALLAX, set like the other five tools' lockups: a system sans at weight 800 with wide tracking, stretched to a fixed length, so the six read as one set. The UI's header keeps its lowercase monospace word. An image can't see the page's theme, so the README picks the file with a `<picture>`: [lockup-animated-dark.svg](lockup-animated-dark.svg) on dark pages, [lockup-animated-light.svg](lockup-animated-light.svg) on light ones. It is the README's heading.

## The agents

Focus, Reticle, Maker, Second Eye and Field, one pixel portrait each on its tile, names under them.

| File | What it is | Where it is used |
|---|---|---|
| [party-animated-light.svg](party-animated-light.svg), [party-animated-dark.svg](party-animated-dark.svg) | The five in a row, taking turns the way a task moves through them. | The README's "What it does", picked by a `<picture>`. |
| [party.svg](party.svg) | The same row, still. | Nothing in the repo today; the still version of the row above. |

## The flow

[flow.svg](flow.svg): how a task moves, from the typed request to your accept, on its own tile with fixed colors, so it reads the same on light and dark pages. The README's "What it does".

## The demo

[demo.gif](demo.gif): the UI with scripted agents, dark theme, about fifteen seconds. Recorded by `scripts/demo.py`; the README shows it under the status line.

## The social preview

[social.html](social.html) is the source: 1280 by 640, the P, the word PARALLAX in the lockup's face and the tagline on the UI's tile color. [social.png](social.png) is its render, uploaded by hand as the GitHub repository's social preview. There is no script for the render; the current PNG was made on 2026-10-25 with a headless Chromium on Linux, where DejaVu Sans Bold stood in for the system sans.
