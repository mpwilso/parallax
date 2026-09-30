"""Every tracked Markdown file renders the way it reads: no angle-bracket placeholder sits outside
code (GitHub would swallow it as an HTML tag), and every relative link and anchor resolves."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# the HTML the docs use on purpose; anything else in angle brackets outside code is a placeholder
HTML_TAGS = {"a", "br", "code", "details", "img", "p", "picture", "source", "strong", "sub", "summary",
             "table", "tr", "td", "th", "em", "b", "i", "kbd", "div", "span", "sup"}


def markdown_files() -> list[Path]:
    out = subprocess.run(["git", "-C", str(ROOT), "ls-files", "-z", "*.md"], capture_output=True, text=True, check=True)
    return [ROOT / p for p in out.stdout.split("\0") if p]


def prose(text: str) -> str:
    """The text with fenced blocks, code spans and HTML comments blanked, line structure kept."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$", blank, text, flags=re.S | re.M)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    return re.sub(r"(`+)(?!`).*?(?<!`)\1(?!`)", blank, text, flags=re.S)


def placeholders(text: str) -> list[str]:
    out = []
    for m in re.finditer(r"<([^<>\n]+)>", prose(text)):
        inner = m.group(1)
        name = re.match(r"/?([A-Za-z][\w-]*)", inner)
        if name and name.group(1).lower() in HTML_TAGS and (inner.startswith("/") or inner == name.group(1)
                                                                or inner[len(name.group(1))] in " \t\n/"):
            continue
        if re.match(r"https?://", inner):
            continue  # an autolink
        out.append(m.group(0))
    return out


def slug(heading: str) -> str:
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def anchors(md: Path) -> set[str]:
    out = set()
    for m in re.finditer(r"^#{1,6}\s+(.+?)\s*#*$", prose(md.read_text()), re.M):
        out.add(slug(re.sub(r"`", "", m.group(1))))
    for m in re.finditer(r'\b(?:id|name)="([^"]+)"', md.read_text()):
        out.add(m.group(1))
    return out


def links(text: str) -> list[str]:
    text = prose(text)
    found = [m.group(1) for m in re.finditer(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)", text)]
    found += [m.group(1) for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)\)", text)]
    found += [m.group(2) for m in re.finditer(r'\b(href|src|srcset)="([^"]+)"', text)]
    return found


def test_no_placeholder_outside_code_in_any_markdown_file():
    bad = {}
    for md in markdown_files():
        if md.relative_to(ROOT).parts[:2] == ("docs", "tasks"):
            continue  # task records are hashed in the ledger, so they're kept as written
        found = placeholders(md.read_text())
        if found:
            bad[str(md.relative_to(ROOT))] = found
    assert not bad, f"angle-bracket placeholders outside code (GitHub renders them as tags): {bad}"


def test_the_check_sees_a_placeholder_and_not_code_or_html():
    assert placeholders("run `parallax show <task>`\n\n```\nsu - <you>\n```\n<p align=\"center\"><img src=\"x\"></p> <!-- <x> -->") == []
    assert placeholders("run parallax show <task> then su - <you>") == ["<task>", "<you>"]


def test_every_relative_link_and_anchor_resolves():
    bad = []
    for md in markdown_files():
        for target in links(md.read_text()):
            if re.match(r"(https?:|mailto:)", target):
                continue
            path, _, anchor = target.partition("#")
            dest = (md.parent / path).resolve() if path else md
            if not dest.exists():
                bad.append(f"{md.relative_to(ROOT)}: {target} (missing {path})")
            elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
                bad.append(f"{md.relative_to(ROOT)}: {target} (no heading #{anchor})")
    assert not bad, "\n".join(bad)


def test_parallax_reviews_itself_with_the_general_template_plus_one_pass_for_its_own_rules():
    """The template ships with Parallax and is what `parallax init` and evals use. Parallax's own
    REVIEW.md is that template plus pass 6, without the housekeeping note, and nothing else."""
    from parallax import review
    own = (ROOT / "REVIEW.md").read_text().splitlines(keepends=True)
    extra = [line for line in own if line.startswith("6. Parallax's own rules:")]
    # the template's housekeeping note (2026-09-30) isn't in Parallax's own file, which keeps its rules
    note = "Housekeeping, such as a changelog entry, docs or a version number left out or not updated, is a note:\n" \
           "minor at most, never blocking, unless a pass above asks for it. It isn't behavior.\n\n"
    assert note in review.TEMPLATE and "Housekeeping" not in "".join(own)
    assert len(extra) == 1 and "".join(line for line in own if line not in extra) == review.TEMPLATE.replace(note, "")
    assert "Parallax" not in review.TEMPLATE.split("## Passes", 1)[1]  # no Parallax rule in the general passes
