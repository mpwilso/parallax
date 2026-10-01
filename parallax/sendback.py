"""A send-back changes only what your reason asks for.

When you send a task back (a reject at Ready, or send back on a card), Focus gets the version it
drafted before, with your reason, and is told to change only what the reason asks. After its redraft,
code compares the intent's outcomes, constraints and scope with that version: anything removed or
rewritten that the reason doesn't mention fails the draft check, named, so Focus puts it back. Text
kept word for word with more added to it is kept; a marker taken off is a rewrite.
Real case: fb461d's note said "keep everything else as it is", and the redraft dropped the markers,
the stub constraint and the scope.
"""
from __future__ import annotations

import re

from . import lint, status
from .core import Project

NUMBERS = re.compile(r"\boutcomes?\s+((?:\d+(?:\s*(?:,|and|or|to|through|-)\s*)?)+)", re.I)


def previous(project: Project, task_id: str) -> dict[str, str]:
    """The version you sent back, by doc, if this attempt began with your send-back: {doc: text}."""
    from .since import _kept
    entries = [e for e in project.ledger.entries() if e["data"].get("task") == task_id]
    attempt = status.attempt(entries, task_id)
    if not attempt or attempt[0]["kind"] != "task.redraft":
        return {}
    folder = _kept(project, task_id, sum(e["kind"] == "task.redraft" for e in entries))
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted(folder.glob("*.md"))} if folder.is_dir() else {}


def reason(project: Project, task_id: str) -> str:
    attempt = status.attempt([e for e in project.ledger.entries() if e["data"].get("task") == task_id], task_id)
    return attempt[0]["reason"] if attempt and attempt[0]["kind"] == "task.redraft" else ""


def _norm(text: str) -> str:
    return " ".join(text.split())


def outcomes(intent: str) -> dict[str, str]:
    """{number: the outcome's text}, continuation lines included."""
    lines = intent.splitlines()
    out: dict[str, str] = {}
    current = None
    for _, line in lint._section_lines(lines, lint._sections(lines, 0), "Outcome") or []:
        m = re.match(r"^\s*(\d+)[.)]\s+(.*)$", line)
        if m:
            current = m.group(1)
            out[current] = m.group(2)
        elif current and line.strip():
            out[current] += " " + line.strip()
    return {k: _norm(v) for k, v in out.items()}


def constraints(intent: str) -> list[str]:
    lines = intent.splitlines()
    return [_norm(re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", line)) for _, line in
            lint._section_lines(lines, lint._sections(lines, 0), "Constraints") or [] if line.strip()]


def _numbers(said: str) -> set[str]:
    out: set[str] = set()
    for m in NUMBERS.finditer(said):
        nums = [int(n) for n in re.findall(r"\d+", m.group(1))]
        ranged = re.search(r"\d+\s*(?:to|through|-)\s*\d+", m.group(1))
        out.update(str(n) for n in (range(nums[0], nums[-1] + 1) if ranged and len(nums) == 2 else nums))
    return out


def _grams(text: str) -> set[tuple[str, ...]]:
    words = re.findall(r"[a-z0-9_./-]+", text.lower())
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


def _names(text: str, said: str) -> bool:
    """The reason names this line: they share three words in a row."""
    return bool(_grams(text) & _grams(said))


def _kept_in(text: str, lines: list[str]) -> bool:
    """Nothing of it was lost: it's there word for word, alone or with more added to it."""
    return any(text in line for line in lines)


def problems(before: str, after: str, said: str) -> list[str]:
    """What the redraft removed or rewrote that your reason doesn't ask to change, one line each."""
    low, out = said.lower(), []
    asked_nums = _numbers(said)
    all_outcomes = re.search(r"\boutcomes?\b", low) and not asked_nums  # "the outcome", with no number, names them all
    now = list(outcomes(after).values())
    for n, text in outcomes(before).items():
        if _kept_in(text, now) or n in asked_nums or all_outcomes or _names(text, said):
            continue
        out.append(f'outcome {n} read "{text}" before this redraft, and the human\'s reason doesn\'t ask to change it: '
                   "put it back word for word")
    kept = constraints(after)
    for text in constraints(before):
        if _kept_in(text, kept) or "constraint" in low or _names(text, said):
            continue
        out.append(f'the constraint "{text}" is gone or rewritten, and the human\'s reason doesn\'t ask to change it: '
                   "put it back word for word")
    scope = set(lint.scope_of(after))
    for path in lint.scope_of(before):
        if path in scope or "scope" in low or path.lower() in low:
            continue
        out.append(f"the scope lost {path}, and the human's reason doesn't ask to change the scope: put it back")
    return out


def material(project: Project, task_id: str, doc: str) -> str:
    """For Focus: the version sent back, and the rule. Empty unless this attempt is a send-back."""
    text = previous(project, task_id).get(doc)
    if not text:
        return ""
    return (f"Your previous draft of this file, which the human sent back (data):\n{text}\n\n"
            "Change only what the human's reason asks for. Keep every other outcome, constraint, scope entry and "
            "marker word for word.")
