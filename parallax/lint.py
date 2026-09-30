"""`parallax lint`: the output shape, checked by code. A lint failure blocks the output.

Two modes:
- a report (inbox item, record, summary): the full header (Type, Bottom line, Not looked at,
  Next), only the known sections in order, and word caps;
- a lifecycle file (docs/tasks/<id>/intent.md, spec.md, plan.md): only Bottom line and Not looked
  at at the top, no word caps, plus the file's own fields (intent's kind and size, the plan's
  toml block). Type and Next are shown at display time, so approved files never change.

Every problem is (line number, message). No problems means ok.
"""
from __future__ import annotations

import re
import tomllib
from pathlib import Path, PurePosixPath

from .guard import is_protected

TYPES = ("Decision needed", "Recommendation", "FYI")
HEADER = ("Type", "Bottom line", "Not looked at", "Next")
LIFECYCLE_HEADER = ("Bottom line", "Not looked at")
SECTIONS = ("Decisions", "Changed since last time", "Found", "Recommended", "Details")
LIFECYCLE_DOCS = ("intent", "spec", "plan")
KINDS = ("bug", "feature", "docs", "chore")
SIZES = ("small", "large")
INTENT_SECTIONS = ("Problem", "Outcome", "Constraints")
HEADER_WORDS, BODY_WORDS = 40, 150
EM_DASH = "\u2014"

DECIDE = re.compile(r"^Decide: .+[.?] Recommend: .+\. Blocks: .+\.$")
WHOSE = re.compile(r"^(Whose call|Why a human): .+\.$")  # the two lines under every question
FILE_LINE = re.compile(r"([\w./-]+\.\w+):(\d+)")
LEDGER_ID = re.compile(r"\bledger ([0-9a-f]{8})\b")
LIST_ITEM = re.compile(r"^\s*(?:[-*]|\d+\.)\s+(.*)$")
FIELD = re.compile(r"^(kind|size|title|scope|budget):\s*(.*?)\s*$", re.I)
OUTCOME_ITEM = re.compile(r"^\s*(\d+)[.)]\s+\S")
OUTCOME_KIND = re.compile(r"^\s*\d+[.)]\s+\**(asked|inferred)\**\s*:", re.I)  # whose outcome it is: the person's, or Focus's
HOST = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")

PLAN_FIELDS = {
    "files": list, "tests": list, "lines_changed": int, "domains": list, "outside_reads": list,
    "binaries": list, "symlinks": list, "dependencies": list, "review_tightening": str,
    "estimated_cost_usd": (int, float), "budget_cap_usd": (int, float), "covers": dict, "user_flows": list,
}
OPTIONAL_PLAN_FIELDS = {"user_flows": []}  # plans drafted before a field existed still read
PLAN_TEMPLATE = '''```toml
files = ["path/to/file.py"]      # every file the change touches
tests = ["tests/test_file.py"]   # the tests that prove it's done
lines_changed = 40               # expected size of the diff
domains = []                     # network domains needed; default none
outside_reads = []               # paths outside the worktree the build must read
binaries = []                    # binary files it will add
symlinks = []                    # symlinks it will add
dependencies = []                # new dependencies
review_tightening = ""           # extra review rule for this task only; can't loosen REVIEW.md
estimated_cost_usd = 1.50
budget_cap_usd = 2.00
covers = { "1" = ["tests/test_file.py"] }   # each numbered outcome in the intent -> the tests or steps that prove it
user_flows = []                  # outcome numbers a person checks by going through the app's UI, like ["1"]
```'''

Problem = tuple[int, str]


def _clean(line: str) -> str:
    return line.replace("**", "").strip()


def _header(lines: list[str], keys: tuple[str, ...]) -> tuple[dict[str, str], int, list[Problem]]:
    """Parse the header lines in order. Returns (values, index after header, problems)."""
    problems: list[Problem] = []
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].startswith("# "):  # a title line may come first
        i += 1
        while i < len(lines) and not lines[i].strip():
            i += 1
    values: dict[str, str] = {}
    for key in keys:
        m = re.match(rf"^{re.escape(key)}:\s*(.*)$", _clean(lines[i])) if i < len(lines) else None
        if not m:
            problems.append((i + 1, f"header: expected '{key}:' here"))
            return values, i, problems
        values[key] = m.group(1).strip()
        if not values[key]:
            problems.append((i + 1, f"header: {key} is empty" + (". write 'nothing' if so" if key == "Not looked at" else "")))
        i += 1
    if i < len(lines) and re.match(r"^(Type|Bottom line|Not looked at|Next):", _clean(lines[i])):
        problems.append((i + 1, "header: unexpected header line"))
    if values.get("Type") and values["Type"] not in TYPES:
        problems.append((1, f"header: Type must be one of {', '.join(TYPES)}"))
    bottom = values.get("Bottom line", "")
    if len([s for s in re.split(r"(?<=[.!?])\s+", bottom) if s]) > 1:
        problems.append((1, "header: Bottom line must be one sentence"))
    return values, i, problems


def _words(lines: list[str]) -> int:
    return sum(len(_clean(line).split()) for line in lines)


def _sections(lines: list[str], start: int, bare: bool = False) -> list[tuple[str, int]]:
    """(name, line index) for each level-two heading after the header.

    With bare, a line that is exactly a known section name counts too: plain CLI output.
    """
    out = []
    fenced = False
    for i in range(start, len(lines)):
        if lines[i].lstrip().startswith("```"):
            fenced = not fenced
        if fenced:
            continue
        m = re.match(r"^##\s+(.+?)\s*$", lines[i])
        if m:
            out.append((m.group(1), i))
        elif bare and lines[i].strip() in SECTIONS:
            out.append((lines[i].strip(), i))
    return out


def _section_lines(lines: list[str], sections: list[tuple[str, int]], name: str) -> list[tuple[int, str]]:
    """The lines of one section, fenced code left out."""
    for n, (s, i) in enumerate(sections):
        if s == name:
            end = sections[n + 1][1] if n + 1 < len(sections) else len(lines)
            out, fenced = [], False
            for j in range(i + 1, end):
                if lines[j].lstrip().startswith("```"):
                    fenced = not fenced
                elif not fenced:
                    out.append((j, lines[j]))
            return out
    return []


def _cites(item: str, root: Path | None, ledger_ids: set[str] | None) -> bool:
    if "Unverified" in item:
        return True
    for m in LEDGER_ID.finditer(item):
        if ledger_ids is not None and m.group(1) in ledger_ids:
            return True
    for m in FILE_LINE.finditer(item):
        path = (root or Path.cwd()) / m.group(1)
        if path.is_file():
            n = int(m.group(2))
            if 1 <= n <= len(path.read_text(encoding="utf-8", errors="replace").splitlines()):
                return True
    return False


def lint_report(text: str, *, revisit: bool = False, root: Path | None = None,
                ledger_ids: set[str] | None = None) -> list[Problem]:
    lines = text.splitlines()
    values, body_start, problems = _header(lines, HEADER)
    problems += _em_dashes(lines)
    if _words(lines[:body_start]) >= HEADER_WORDS:
        problems.append((1, f"header is {_words(lines[:body_start])} words; keep it under {HEADER_WORDS}"))

    sections = _sections(lines, body_start, bare=True)
    names = [s for s, _ in sections]
    for name, i in sections:
        if name not in SECTIONS:
            problems.append((i + 1, f"unknown section '{name}'. use {', '.join(SECTIONS)}"))
    known = [SECTIONS.index(n) for n in names if n in SECTIONS]
    if known != sorted(known) or len(set(known)) != len(known):
        problems.append((body_start + 1, f"sections out of order or repeated. order: {', '.join(SECTIONS)}"))
    if "Decisions" in names and "Recommended" in names:
        problems.append((body_start + 1, "Recommended only when there are no Decisions"))
    if revisit and "Changed since last time" not in names:
        problems.append((body_start + 1, "a revisit needs a Changed since last time section"))

    for j, line in _section_lines(lines, sections, "Decisions"):
        m = LIST_ITEM.match(line)
        body = (m.group(1) if m else line).strip()
        if body and not DECIDE.match(body) and not WHOSE.match(body):
            problems.append((j + 1, "decision line must read: Decide: <question>. Recommend: <option>. Blocks: <what, or nothing>. "
                                    "or Whose call: <who>. or Why a human: <why>."))
    for j, line in _section_lines(lines, sections, "Found"):
        m = LIST_ITEM.match(line)
        if m and not _cites(m.group(1), root, ledger_ids):
            problems.append((j + 1, "Found item cites no source that exists (file:line or ledger id). or label it Unverified"))

    details = next((i for s, i in sections if s == "Details"), len(lines))
    heads = {i for _, i in sections}
    body = [line for i, line in enumerate(lines[body_start:details], body_start) if i not in heads]
    if _words(body) >= BODY_WORDS:
        problems.append((body_start + 1, f"body is {_words(body)} words; keep it under {BODY_WORDS}, move depth to Details"))
    return sorted(problems)


def _em_dashes(lines: list[str]) -> list[Problem]:
    return [(i + 1, "no em dashes") for i, line in enumerate(lines) if EM_DASH in line]


def intent_fields(text: str) -> dict[str, str]:
    """kind, size and title from an intent file. Missing ones are left out."""
    out = {}
    for line in text.splitlines():
        m = FIELD.match(_clean(line))
        if m and m.group(1).lower() not in out:
            out[m.group(1).lower()] = m.group(2)
    return out


def plan_block(text: str) -> tuple[dict | None, int, str | None]:
    """The plan's last ```toml block: (data, line of the block, why it's unusable)."""
    blocks = [m for m in re.finditer(r"^```toml[ \t]*\n(.*?)^```[ \t]*$", text, re.M | re.S)]
    if not blocks:
        return None, len(text.splitlines()), "plan has no ```toml block at the end"
    m = blocks[-1]
    line = text[:m.start()].count("\n") + 1
    if text[m.end():].strip():
        return None, line, "the ```toml block must be the last thing in the plan"
    try:
        data = tomllib.loads(m.group(1))
        for key, default in OPTIONAL_PLAN_FIELDS.items():
            data.setdefault(key, list(default))
        return data, line, None
    except tomllib.TOMLDecodeError as err:
        return None, line, f"the toml block doesn't parse: {err}"


def check_plan_data(data: dict) -> list[str]:
    """What's wrong with the plan's toml block, if anything."""
    out = []
    missing = [k for k in PLAN_FIELDS if k not in data and k not in OPTIONAL_PLAN_FIELDS]
    unknown = [k for k in data if k not in PLAN_FIELDS]
    if missing:
        out.append(f"plan block is missing {', '.join(missing)}")
    if unknown:
        out.append(f"plan block has unknown fields {', '.join(unknown)}")
    for key, kind in PLAN_FIELDS.items():
        if key not in data:
            continue
        v = data[key]
        if isinstance(v, bool) or not isinstance(v, kind):
            out.append(f"{key} has the wrong type")
        elif kind is list and not all(isinstance(x, str) and x.strip() for x in v):
            out.append(f"{key} must be a list of non-empty strings")
        elif kind is dict and not all(isinstance(x, list) and x and all(isinstance(y, str) and y.strip() for y in x)
                                      for x in v.values()):
            out.append(f"{key} must map each outcome number to a list of tests or steps")
    if out:
        return out
    if not data["files"]:
        out.append("files is empty. list every file the change touches")
    for key in ("files", "tests", "binaries", "symlinks"):
        for p in data[key]:
            pp = PurePosixPath(p.replace("\\", "/"))
            if pp.is_absolute() or ".." in pp.parts:
                out.append(f"{key}: {p} must be a path inside the repo")
            elif is_protected(p):
                out.append(f"{key}: {p} is protected. the maker can never write it")
    for d in data["domains"]:
        if not HOST.match(d.lower()):
            out.append(f"domains: {d} isn't a plain host name")
    if data["lines_changed"] < 0:
        out.append("lines_changed can't be negative")
    if data["estimated_cost_usd"] <= 0 or data["budget_cap_usd"] <= 0:
        out.append("estimated_cost_usd and budget_cap_usd must be more than 0")
    elif data["budget_cap_usd"] < data["estimated_cost_usd"]:
        out.append("budget_cap_usd is below estimated_cost_usd")
    return out


def lint_lifecycle(text: str, doc: str) -> list[Problem]:
    lines = text.splitlines()
    _, _, problems = _header(lines, LIFECYCLE_HEADER)
    problems += _em_dashes(lines)
    if doc == "intent":
        fields = intent_fields(text)
        if fields.get("kind") not in KINDS:
            problems.append((1, f"intent needs 'kind:' set to one of {', '.join(KINDS)}"))
        if fields.get("size") not in SIZES:
            problems.append((1, f"intent needs 'size:' set to one of {', '.join(SIZES)}"))
        if not fields.get("title"):
            problems.append((1, "intent needs 'title:', a short phrase like 'fixing the README install steps'"))
        if not scope_of(text):
            problems.append((1, "intent needs 'scope:', the paths or globs the work may touch, comma separated"))
        if "budget" in fields and _money(fields["budget"]) is None:
            problems.append((1, "intent's 'budget:' must be a dollar amount, like 4.00"))
        if not outcomes_of(text):
            problems.append((1, "intent's '## Outcome' needs a numbered list: 1. ..., 2. ..."))
        for n, kind in outcome_kinds(text).items():
            if kind is None:
                problems.append((1, f"outcome {n} needs 'asked:' (the person's words state or clearly imply it) or "
                                    "'inferred:' (your own addition) right after its number"))
        names = [s for s, _ in _sections(lines, 0)]
        for name in INTENT_SECTIONS:
            if name not in names:
                problems.append((1, f"intent needs a '## {name}' section"))
    elif doc == "plan":
        data, line, why = plan_block(text)
        if why:
            problems.append((line, why))
        else:
            problems += [(line, p) for p in check_plan_data(data)]
    return sorted(problems)


def lifecycle_doc(path: Path) -> str | None:
    """'intent', 'spec' or 'plan' for a file at docs/tasks/<id>/<doc>.md, else None."""
    parts = Path(path).resolve().parts
    if len(parts) >= 4 and parts[-4:-2] == ("docs", "tasks") and Path(path).suffix == ".md":
        return Path(path).stem if Path(path).stem in LIFECYCLE_DOCS else None
    return None


def lint_file(path: Path, root: Path | None = None, ledger_ids: set[str] | None = None) -> list[Problem]:
    text = Path(path).read_text(encoding="utf-8")
    doc = lifecycle_doc(path)
    return lint_lifecycle(text, doc) if doc else lint_report(text, root=root, ledger_ids=ledger_ids)


def report(type_: str, bottom: str, not_looked_at: str, next_: str, found: list[str] = (),
           details: list[str] = (), changed: list[str] = (), decisions: list[str] = ()) -> str:
    """A report in the output shape. Callers lint it before showing it."""
    lines = [f"Type: {type_}", f"Bottom line: {bottom}", f"Not looked at: {not_looked_at}", f"Next: {next_}"]
    if decisions:
        lines += ["Decisions", *[f"- {d}" for d in decisions]]
    if changed:
        lines += ["Changed since last time", *[f"- {c}" for c in changed]]
    if found:
        lines += ["Found", *[f"- {f}" for f in found]]
    if details:
        lines += ["Details", *[f"- {d}" for d in details]]
    return "\n".join(lines)


def one_sentence(text: str) -> str:
    """Text made safe for a Bottom line: one sentence, ending in a full stop."""
    text = " ".join(str(text).replace(EM_DASH, ",").split())
    return re.sub(r"(?<=[.!?])\s+", "; ", text).rstrip(".!?;, ") + "."


def shaped(type_: str, bottom: str, gaps: list[tuple[str, str]], next_: str, found: list[str] = (),
           changed: list[str] = (), who: str = "the drafters", extra: list[str] = (),
           decisions: list[str] = (), details: list[str] = ()) -> str:
    """A report that carries others' Not looked at, never replaces it.

    gaps: (inline text, cited text) pairs. Inline in the header when that fits its cap; otherwise
    each cited text goes under Found. extra: Found items that move to Details first, and then the
    cited gaps too, if the body would break its cap.
    """
    found, extra, changed, details = list(found), list(extra), list(changed), list(details)
    kw = {"changed": changed, "decisions": list(decisions)}

    def fits(text: str, cap: str) -> bool:
        return not any(cap in m for _, m in lint_report(text))

    if gaps:
        inline = "; ".join(g.rstrip(".") for g, _ in gaps) + "."
        text = report(type_, bottom, inline, next_, found + extra, details, **kw)
        if fits(text, "header is"):
            if fits(text, "body is"):
                return text
            return report(type_, bottom, inline, next_, found, extra + details, **kw)
        cited, header = [c for _, c in gaps], f"see Found ({len(gaps)})"
        for f, d in ((found + extra + cited, details), (found + cited, extra + details)):
            text = report(type_, bottom, header, next_, f, d, **kw)
            if fits(text, "body is"):
                return text
        return report(type_, bottom, f"see Details ({len(gaps)})", next_, found, extra + cited + details, **kw)
    text = report(type_, bottom, "nothing", next_, found + extra, details, **kw)
    return text if fits(text, "body is") else report(type_, bottom, "nothing", next_, found, extra + details, **kw)


def _money(text: str) -> float | None:
    try:
        v = float(text.strip().lstrip("$"))
    except ValueError:
        return None
    return v if v > 0 else None


def scope_of(intent: str) -> list[str]:
    """The intent's scope: the paths or globs the work may touch."""
    return [p.strip().strip("`") for p in intent_fields(intent).get("scope", "").split(",") if p.strip().strip("`")]


def budget_of(intent: str) -> float | None:
    """A budget the human named, if the intent carries one."""
    return _money(intent_fields(intent).get("budget", ""))


def outcomes_of(intent: str) -> list[str]:
    """The numbers of the intent's numbered outcomes."""
    lines = intent.splitlines()
    return [m.group(1) for _, line in _section_lines(lines, _sections(lines, 0), "Outcome")
            if (m := OUTCOME_ITEM.match(line))]


def unmark(outcomes: str) -> str:
    """Outcome lines without Focus's asked or inferred mark: for agents that judge the outcome as stated."""
    return re.sub(r"(?im)^(\s*\d+[.)]\s+)\**(?:asked|inferred)\**\s*:\s*", r"\1", outcomes)


def outcome_kinds(intent: str) -> dict[str, str | None]:
    """outcome number -> "asked", "inferred", or None when the outcome isn't marked (intents before 2026-09-30)."""
    lines = intent.splitlines()
    out = {}
    for _, line in _section_lines(lines, _sections(lines, 0), "Outcome"):
        if (m := OUTCOME_ITEM.match(line)):
            k = OUTCOME_KIND.match(line)
            out[m.group(1)] = k.group(1).lower() if k else None
    return out


# normalizing and fitting: Parallax never shows or saves its own output failing its own lint -----------

_HEAD = re.compile(r"^\**\s*(Type|Bottom line|Not looked at|Next)\s*:\**\s*(.*)$", re.I)


def normalize(text: str, doc: str | None = None) -> str:
    """Repair the slips drafters make, before a drafted file is saved.

    Unwraps a fence around the whole reply, trims trailing spaces, pulls the header lines
    together at the top (a blank line inside it failed lint in task ef4163), and turns em
    dashes into commas. Content is otherwise untouched; what still fails goes back to the drafter.
    """
    m = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*", text, re.S)
    text = m.group(1) if m else text
    text = text.replace(" " + EM_DASH + " ", ", ").replace(EM_DASH, ", ")
    lines = [line.rstrip() for line in text.strip().splitlines()]
    keys = LIFECYCLE_HEADER if doc in LIFECYCLE_DOCS else HEADER
    head, rest, seen = {}, [], 0
    for i, line in enumerate(lines):
        hm = _HEAD.match(line) if seen < len(keys) and i < 15 else None
        name = next((k for k in keys if hm and hm.group(1).lower() == k.lower()), None)
        if name and name not in head:
            head[name] = hm.group(2).strip()
            seen += 1
        else:
            rest.append(line)
    if len(head) < len(keys):
        return "\n".join(lines) + "\n"  # can't repair a missing header line; lint says what's missing
    title = [rest.pop(0)] if rest and rest[0].startswith("# ") else []
    while rest and not rest[0].strip():
        rest.pop(0)
    gap = [""] if doc in LIFECYCLE_DOCS and rest else []  # drafted files keep a blank line after the header
    out = title + [f"{k}: {head[k]}" for k in keys] + gap + rest
    return "\n".join(out) + "\n"


def _truncate(text: str, words: int) -> str:
    kept = text.split()[:max(words, 3)]
    return one_sentence(" ".join(kept))


def fit(text: str, root: Path | None = None, ledger_ids: set[str] | None = None) -> tuple[str, list[str]]:
    """A report of Parallax's own, repaired until it lints. Returns (text, what had to be repaired).

    Shortens header fields to the cap, labels uncited Found items Unverified, moves Found items to
    Details when the body is over its cap, and replaces em dashes. If none of that is enough, it
    falls back to the header alone with everything else under Details.
    """
    fixed: list[str] = []
    text = normalize(text)
    if EM_DASH in text:
        text = text.replace(EM_DASH, ",")
    for _ in range(40):
        problems = lint_report(text, root=root, ledger_ids=ledger_ids)
        if not problems:
            return text, fixed
        lines = text.splitlines()
        msg = problems[0][1]
        n = problems[0][0] - 1
        if msg.startswith("header is"):
            fields = [(i, _HEAD.match(line)) for i, line in enumerate(lines[:4])]
            i, hm = max(((i, hm) for i, hm in fields if hm), key=lambda x: len(x[1].group(2).split()))
            lines[i] = f"{hm.group(1)}: {_truncate(hm.group(2), len(hm.group(2).split()) - 4)}"
            fixed.append("shortened the header")
        elif "cites no source" in msg and 0 <= n < len(lines):
            lines[n] = lines[n] + " (Unverified)"
            fixed.append("labeled an uncited item Unverified")
        elif msg.startswith("body is"):
            found = [i for i, line in enumerate(lines) if line.strip() == "Found" or line.strip() == "## Found"]
            items = [i for i in range(found[0] + 1, len(lines)) if LIST_ITEM.match(lines[i])] if found else []
            if not items:
                break
            moved = lines.pop(items[-1])
            if not any(line.strip() in ("Details", "## Details") for line in lines):
                lines.append("Details")
            lines.append(moved)
            fixed.append("moved a Found item to Details")
        elif "one sentence" in msg:
            i = next(i for i, row in enumerate(lines[:4]) if row.startswith("Bottom line:"))
            lines[i] = "Bottom line: " + one_sentence(lines[i][len("Bottom line:"):])
            fixed.append("made the bottom line one sentence")
        else:
            break
        text = "\n".join(lines)
    head = [line for line in text.splitlines()[:4]]
    body = [f"- {line.lstrip('- ')}" for line in text.splitlines()[4:] if line.strip() and line.strip() not in SECTIONS]
    fallback = "\n".join(head + (["Details"] + body if body else []))
    if lint_report(fallback, root=root, ledger_ids=ledger_ids):
        fallback = report("FYI", "Parallax couldn't fit this report into its own shape.", "the report itself",
                          "run parallax log to see the raw record.")
    return fallback, fixed + ["fell back to the header alone"]
