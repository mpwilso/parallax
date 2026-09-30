"""How Markdown renders on GitHub, for the one rule both the docs test and the draft check use: an
angle-bracket placeholder outside code, like <what>, is read as an HTML tag and vanishes."""
from __future__ import annotations

import re

# the HTML the docs use on purpose; anything else in angle brackets outside code is a placeholder
HTML_TAGS = {"a", "br", "code", "details", "img", "p", "picture", "source", "strong", "sub", "summary",
             "table", "tr", "td", "th", "em", "b", "i", "kbd", "div", "span", "sup"}


def prose(text: str) -> str:
    """The text with fenced blocks, code spans and HTML comments blanked, line structure kept."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    text = re.sub(r"^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*$", blank, text, flags=re.S | re.M)
    text = re.sub(r"<!--.*?-->", blank, text, flags=re.S)
    return re.sub(r"(`+)(?!`).*?(?<!`)\1(?!`)", blank, text, flags=re.S)


def placeholder_lines(text: str) -> list[tuple[int, str]]:
    """(line, placeholder) for each angle-bracket placeholder outside code, 1-based lines."""
    body = prose(text)
    out = []
    for m in re.finditer(r"<([^<>\n]+)>", body):
        inner = m.group(1)
        name = re.match(r"/?([A-Za-z][\w-]*)", inner)
        if name and name.group(1).lower() in HTML_TAGS and (inner.startswith("/") or inner == name.group(1)
                                                                or inner[len(name.group(1))] in " \t\n/"):
            continue
        if re.match(r"https?://", inner):
            continue  # an autolink
        out.append((body.count("\n", 0, m.start()) + 1, m.group(0)))
    return out


def placeholders(text: str) -> list[str]:
    return [p for _, p in placeholder_lines(text)]
