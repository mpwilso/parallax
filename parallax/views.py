"""What the UI shows, built from the ledger. Plain data, no HTTP, so it's testable.

The board is every task by state. A task's card is exactly `parallax show`, parsed into its
sections, so the terminal and the browser can never disagree about a task.
"""
from __future__ import annotations

import re

from . import decide, inbox, lifecycle, live, show, status
from .core import Project

MAX_TEXT = 200_000
SECTIONS = ("Decisions", "Changed since last time", "Found", "Recommended", "Details")


RISK = {"guard": 0, "scope": 1, "conflict": 1, "stuck": 2, "checker": 2, "tests": 2, "rework": 3, "cap": 3,
        "drafting": 3, "launch": 4, "review": 4}  # what needs you most comes first; Ready comes last


def board(project: Project) -> dict:
    """The queue: what waits on you (riskiest first, then oldest), what's working, and what's done.

    columns: every task by state, kept for the terminal's view of the same thing."""
    columns: dict[str, list[dict]] = {state: [] for state in status.BOARD}
    for tid, t in project.tasks().items():
        if not t.get("intent"):
            continue
        state = status.board(t["status"])
        columns[state].append({"task": tid, "title": inbox.title(project, tid), "status": t["status"], "state": state,
                               "cost_usd": round(t.get("cost_usd") or 0, 2), "last": t.get("last")})
    waiting = []
    for item in columns["needs you"] + columns["ready"]:
        dec = decide.decision(project, item["task"])
        item["kind"] = dec.kind if dec else "ready"
        item["line"] = _headline(project, item["task"])
        item["secret"] = bool(dec and dec.item and any(f.get("secret") and f.get("size") for f in dec.item["data"].get("files") or []))
        waiting.append(item)
    waiting.sort(key=lambda i: (not i["secret"], RISK.get(i["kind"], 5), i["last"] or ""))
    working = [dict(i, line=live.line(project, i["task"])) for s in ("drafting", "building", "checking")
               for i in columns[s]]
    done = sorted(columns["done"], key=lambda i: i["last"] or "", reverse=True)
    for item in done:
        item["merge"] = item["status"] == "accepted"  # accepted, and the merge is still yours
    return {"columns": columns, "waiting": waiting, "working": working, "done": done, "count": len(waiting)}


def _headline(project: Project, task_id: str) -> str:
    bottom = parse_report(show.report(project, task_id))["bottom"]
    for prefix in ("Needs you: ", "Ready: "):
        if bottom.startswith(prefix):
            return bottom[len(prefix):]
    return bottom


def parse_report(text: str) -> dict:
    """The output shape, as data: the four header fields and each section's items."""
    out: dict = {"type": "", "bottom": "", "not_looked_at": "", "next": "", "sections": {}}
    keys = {"Type": "type", "Bottom line": "bottom", "Not looked at": "not_looked_at", "Next": "next"}
    current = None
    for line in text.splitlines():
        m = re.match(r"^(Type|Bottom line|Not looked at|Next):\s*(.*)$", line)
        if m and current is None:
            out[keys[m.group(1)]] = m.group(2)
        elif line.strip().lstrip("# ").strip() in SECTIONS:
            current = line.strip().lstrip("# ").strip()
            out["sections"][current] = []
        elif current and line.startswith("- "):
            out["sections"][current].append(line[2:])
    return out


GAP = re.compile(r"(did not look at|not looked at): ", re.I)
CITE = re.compile(r"\s*\((ledger [0-9a-f]+|[\w./-]+:\d+|Unverified)\)$")


def card(project: Project, task_id: str) -> dict:
    """Everything the decision card needs: the report, and what you can do from it.

    Same facts as `parallax show`, arranged for a decision: the gaps the header could only point to
    come back under Not looked at, and each item's citation is split off so the page can quiet it."""
    t = project.task(task_id)
    report = parse_report(show.report(project, task_id))
    dec = decide.decision(project, task_id)
    files = []
    if dec is not None:
        actions = {"kind": "decide", "question": dec.question, "recommend": dec.recommend,
                   "options": [{"name": o.name, "does": o.does, "needs_reason": o.needs_reason} for o in dec.options]}
        files = dec.item["data"].get("files") or [] if dec.item else []
    elif t["status"] == "ready":
        actions = {"kind": "ready"}
    else:
        actions = {"kind": "none"}
    merge = ""
    if t["status"] == "accepted":
        from .accept import merge_command
        accepted = [e for e in project.ledger.entries() if e["kind"] == "task.accepted" and e["data"]["task"] == task_id]
        merge = merge_command(accepted[-1]) if accepted else ""
    unseen = [report["not_looked_at"]]
    if re.match(r"^see (Found|Details) \(\d+\)$", report["not_looked_at"]):
        where = "Found" if "Found" in report["not_looked_at"] else "Details"
        items = report["sections"].get(where, [])
        gaps = [i for i in items if GAP.search(i)]
        report["sections"][where] = [i for i in items if i not in gaps]
        unseen = [GAP.split(i, 1)[-1] for i in gaps] or unseen
    found = [_cited(i) for i in report["sections"].get("Found", []) if not i.startswith("the work: ")]  # the title says it
    for f in found:
        f["text"] = re.sub(r"; parallax diff \w+ shows it$", "", f["text"])  # the page has the change a click away
    if files:  # the table under the question says it, file by file
        found = [f for f in found if f["cite"] != f"ledger {dec.item['id']}"]
    state = status.board(t["status"])
    entries = status.attempt(project.ledger.entries(), task_id)
    return {"task": task_id, "title": inbox.title(project, task_id), "status": t["status"], "state": state,
            "cost_usd": round(t.get("cost_usd") or 0, 2), "report": report, "unseen": [_cited(u) for u in unseen],
            "found": found,
            "changed": [_cited(i) for i in report["sections"].get("Changed since last time", [])],
            "details": [_cited(i) for i in report["sections"].get("Details", [])],
            "actions": actions, "merge": merge, "files": files,
            "live": live.line(project, task_id) if state in ("drafting", "building", "checking") else "",
            "has_change": any(e["kind"] == "check.staged" for e in entries) or t["status"] in ("accepted", "merged"),
            "shots": shots(project, task_id),
            "docs": [d for d in ("intent", "spec", "plan", "record") if lifecycle.found_doc(project, task_id, d)]}


def shots(project: Project, task_id: str) -> list[dict]:
    """What the UI tester saw, this attempt: one screenshot per flow, with what it said about it."""
    from . import uitest
    rec = uitest.recorded(project, task_id)
    if not rec:
        return []
    saw = {f"{f.get('name')}.png": f for f in rec["data"].get("flows") or [] if isinstance(f, dict)}
    folder = uitest.evidence(project, task_id)
    return [{"name": n, "caption": str(saw.get(n, {}).get("saw") or n.removesuffix(".png").replace("-", " ")),
             "works": saw.get(n, {}).get("works")} for n in rec["data"].get("shots") or [] if (folder / n).is_file()]


def shot(project: Project, task_id: str, name: str) -> bytes:
    """One screenshot, only by a name the tester's record lists."""
    from . import uitest
    if name not in {s["name"] for s in shots(project, task_id)}:
        raise ValueError(f"no screenshot {name!r}")
    return (uitest.evidence(project, task_id) / name).read_bytes()


def _cited(item: str) -> dict:
    """{text, cite}: the claim, and the ledger id or file:line it cites."""
    m = CITE.search(item)
    return {"text": item[:m.start()] if m else item, "cite": m.group(1) if m else ""}


def document(project: Project, task_id: str, name: str) -> str:
    """The diff, or one of the task's documents, as plain text."""
    if name == "diff":
        from .cli import _reviewed_diff
        text = _reviewed_diff(project, task_id)
    elif name in ("intent", "spec", "plan", "record"):
        path = lifecycle.found_doc(project, task_id, name)
        text = path.read_text(encoding="utf-8") if path else ""
    else:
        raise ValueError(f"no document {name!r}")
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + f"\n\n(cut here. the rest: parallax diff {task_id})"
    return text
