"""mission.md: who the conductor is, what it checks each pulse, how it works with you.

Human-owned. Agents can't write it (see guard.py). HTML comments are ignored, so the
template's hints don't count as a mission.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MISSION_FILE = "mission.md"
SECTIONS = ("who", "what", "how")

TEMPLATE = """\
# Mission

## who
<!-- Who the conductor is: its role, and what it should know about this project. -->

## what
<!-- What it checks every time it wakes up. One check per line. -->

## how
<!-- The laws for working with you. Makers are given these too. -->
"""


@dataclass
class Mission:
    who: str
    what: str
    how: str

    @property
    def text(self) -> str:
        return "\n\n".join(f"## {name}\n{getattr(self, name)}" for name in SECTIONS if getattr(self, name))


def parse(text: str) -> Mission | None:
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    parts = {name: [] for name in SECTIONS}
    current = None
    for line in text.splitlines():
        heading = re.match(r"^##\s+(\w+)\s*$", line.strip())
        if heading:
            current = heading.group(1).lower() if heading.group(1).lower() in parts else None
            continue
        if current:
            parts[current].append(line)
    mission = Mission(**{k: "\n".join(v).strip() for k, v in parts.items()})
    return mission if any((mission.who, mission.what, mission.how)) else None


def load(root: Path) -> Mission | None:
    """The mission, or None if there's no mission.md or it says nothing yet."""
    path = Path(root) / MISSION_FILE
    return parse(path.read_text(encoding="utf-8")) if path.exists() else None
