"""The README's rules: short, no dashes as punctuation, links that resolve, no hidden placeholders, and
no words that claim more than was measured. Stdlib only, and this file stays ASCII."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"

EM_DASH = chr(0x2014)
EN_DASH = chr(0x2013)
TAGS = {"p", "picture", "source", "img", "b", "details", "summary", "br", "a", "sub", "sup"}


def prose(text: str) -> str:
    """The README as GitHub shows it: code blocks, code spans, HTML comments and quoted lines taken out."""
    text = re.sub(r"^```.*?^```$", "", text, flags=re.S | re.M)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"^>.*$", "", text, flags=re.M)


def slug(heading: str) -> str:
    """GitHub's heading anchors: lower case, punctuation dropped, spaces as hyphens."""
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def headings(text: str) -> list[str]:
    return [re.sub(r"^#+ ", "", line) for line in prose(text).split("\n") if re.match(r"^#{1,6} ", line)]


def problems(text: str, base: Path = ROOT) -> list[str]:
    found = []
    shown = re.sub(r"<details>.*?</details>", "<details></details>", text, flags=re.S)
    lines = shown.count("\n") + (0 if shown.endswith("\n") or not shown else 1)
    if lines >= 155:
        found.append(f"{lines} lines; keep it under 155")
    if EM_DASH in text:
        found.append("an em dash")
    if EN_DASH in text:
        found.append("an en dash")
    anchors = [slug(h) for h in headings(text)]
    for m in re.finditer(r"\]\(([^)\s]+)\)|\b(?:src|srcset|href)=\"([^\"]+)\"", text):
        link = m.group(1) or m.group(2)
        if link.startswith("#"):
            if link[1:] not in anchors:
                found.append(f"an anchor with no heading: {link}")
            continue
        if re.match(r"^(https?:|mailto:)", link):
            continue
        if not (base / re.split(r"[?#]", link)[0]).exists():
            found.append(f"a broken link: {link}")
    text_shown = prose(text)
    for m in re.finditer(r"<\/?([^\s>/]*)[^>]*>", text_shown):
        if m.group(1).lower() not in TAGS:
            found.append(f"a bare angle-bracket placeholder: {m.group(0)}")
    words = re.sub(r"<[^>]*>", "", text_shown)  # tags out, alt text with them: it describes an image
    for m in re.finditer(r"\b(only|first|best)\b", words, flags=re.I):
        found.append(f'"{m.group(1)}" in the prose')
    return found


def test_the_readme_passes_every_rule():
    assert problems(README.read_text(encoding="utf-8")) == []


def test_each_readme_rule_catches_what_it_should():
    assert problems("line\n" * 155) == ["155 lines; keep it under 155"]
    assert problems("<details>\n" + "line\n" * 155 + "</details>\n") == []
    assert problems("## Setup\n\nJump to [setup](#setup) or [the end](#the-end).\n\n```\n## The end\n```\n") == [
        "an anchor with no heading: #the-end"]
    assert problems("A long pause" + EM_DASH + "then more.\n") == ["an em dash"]
    assert problems("A range, 3" + EN_DASH + "5.\n") == ["an en dash"]
    assert problems('See [the notes](docs/nowhere.md) and <img src="docs/brand/missing.svg">.\n') == [
        "a broken link: docs/nowhere.md", "a broken link: docs/brand/missing.svg"]
    assert problems("Show the draft under <the reason>.\n") == ["a bare angle-bracket placeholder: <the reason>"]
    assert problems("Show the draft under `<the reason>`.\n\n```\n<folder>\n```\n") == []
    assert problems('The first tool, and the best.\n<img src="docs/brand/mark.svg" alt="Our only mark">\n') == [
        '"first" in the prose', '"best" in the prose']
    assert problems("```markdown\nFirst question: only the next box?\n```\n> Quoted output: the best one.\n<!-- only here -->\n") == []


def test_the_source_of_this_test_is_ascii():
    Path(__file__).read_bytes().decode("ascii")
