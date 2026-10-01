"""What the UI shows, built from the ledger. Plain data, no HTTP, so it's testable.

The board is every task by state. A task's card is exactly `parallax show`, parsed into its
sections, so the terminal and the browser can never disagree about a task.
"""
from __future__ import annotations

import re
import sys
import traceback

from . import decide, inbox, lifecycle, live, progress, show, stats, status
from .core import Project

MAX_TEXT = 200_000
SECTIONS = ("Decisions", "Changed since last time", "Found", "Recommended", "Details")


RISK = {"guard": 0, "scope": 1, "conflict": 1, "stuck": 2, "tool": 2, "checker": 2, "tests": 2, "rework": 3, "cap": 3,
        "drafting": 3, "budget": 3, "loop": 3, "launch": 4, "review": 4}  # what needs you most comes first; Ready comes last


BROKEN = "couldn't display this task"


def log_broken(task_id: str, where: str) -> None:
    """The traceback goes to the server's terminal; the page and the list show the task as broken."""
    print(f"parallax: {BROKEN} {task_id} ({where})\n{traceback.format_exc()}", file=sys.stderr, flush=True)


def broken_row(task_id: str, t: dict | None = None) -> dict:
    """A row for a task whose own line couldn't be built: its id, and a plain sentence. Nothing else."""
    t = t or {}
    return {"task": task_id, "title": f"Task {task_id}", "status": t.get("status", ""), "state": "needs you",
            "cost_usd": round(t.get("cost_usd") or 0, 2), "last": t.get("last"), "kind": "broken", "secret": False,
            "broken": True, "line": f"Task {task_id}: {BROKEN}. To see why, run parallax show {task_id} in a terminal; every other task still works.",
            "chip": "Can't display", "strip": [], "spend": None, "started": ""}


def board(project: Project) -> dict:
    """The queue: what waits on you (riskiest first, then oldest), what's working, and what's done.
    One task that can't be shown is shown as broken, with its id; every other task shows as usual.

    columns: every task by state, kept for the terminal's view of the same thing."""
    from .build import flag_stale_runs
    from .sessions import reconcile
    reconcile(project)  # the same housekeeping every command runs: a dead task never shows as building
    flag_stale_runs(project)
    columns: dict[str, list[dict]] = {state: [] for state in status.BOARD}
    broken: list[dict] = []
    for tid, t in project.tasks().items():
        if not t.get("intent"):
            continue
        try:
            state = status.board(t["status"])
            columns[state].append({"task": tid, "title": inbox.title(project, tid), "status": t["status"], "state": state,
                                   "cost_usd": round(t.get("cost_usd") or 0, 2), "last": t.get("last")})
        except Exception:
            log_broken(tid, "its title")
            broken.append(broken_row(tid, t))
    waiting = []
    for item in columns["needs you"] + columns["ready"]:
        try:
            dec = decide.decision(project, item["task"])
            item["kind"] = dec.kind if dec else "ready"
            item["line"] = progress.sentence(_headline(project, item["task"]))
            item["secret"] = bool(dec and any(f.get("secret") and f.get("size") for f in decide.scope_files(dec.item)))
            item.update(_row(project, item, dec))
            waiting.append(item)
        except Exception:
            log_broken(item["task"], "its row")
            broken.append(broken_row(item["task"], item))
    waiting.sort(key=lambda i: (not i["secret"], RISK.get(i["kind"], 5), i["last"] or ""))
    working = []
    for s in ("drafting", "building", "checking", "merging"):
        for i in columns[s]:
            try:
                entries = status.attempt(project.ledger.entries(), i["task"])
                what, why, _ = live.doing(entries)
                if s == "merging":
                    from . import merging
                    what, why = (merging.info(project, i["task"]) or {}).get("text", "Merging"), ""
                item = dict(i, line=progress.sentence(what + (f". {why[:1].upper()}{why[1:]}" if why else "")),
                            agent=live.agent_of(what), kind=None)
                item.update(_row(project, item, None))
                working.append(item)
            except Exception:
                log_broken(i["task"], "its row")
                broken.append(broken_row(i["task"], i))
    done = []
    touches = _or_log(lambda: stats.touches(project), {}, "all", "touch counts")
    for item in sorted(columns["done"], key=lambda i: i["last"] or "", reverse=True):
        try:
            item["merge"] = item["status"] == "accepted"  # accepted, and the merge is still yours
            item.update(progress.done_row(project, item["task"], touches))
            done.append(item)
        except Exception:
            log_broken(item["task"], "its done row")
            broken.append(broken_row(item["task"], item))
    waiting = broken + waiting  # a task that can't be shown is one to look at: first, and never silent
    return {"columns": columns, "waiting": waiting, "working": working, "done": done, "count": len(waiting),
            "overview": _or_log(lambda: progress.overview(project), [], "all", "the overview")}


def _or_log(build, fallback, task_id: str, where: str):
    try:
        return build()
    except Exception:
        log_broken(task_id, where)
        return fallback


def _row(project: Project, item: dict, dec) -> dict:
    """What a waiting or working row shows besides its title: chip, strip, spend and start."""
    from . import overlap
    return {"chip": progress.chip(item["state"], item.get("kind")), "strip": progress.strip(project, item["task"], dec),
            "spend": progress.spend(project, item["task"]), "started": progress.started(project, item["task"]),
            "overlaps": [o["task"] for o in overlap.of(project, item["task"])]}


def _headline(project: Project, task_id: str) -> str:
    bottom = parse_report(show.report(project, task_id))["bottom"]
    for prefix in ("Needs you: ", "Ready again after your reject: ", "Ready: "):
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


GAP = re.compile(r"(did not look at|not looked at|didn't check): ", re.I)
CITE = re.compile(r"\s*\((ledger [0-9a-f]+|[\w./-]+:\d+|Unverified)\)$")


def card(project: Project, task_id: str) -> dict:
    """The card, or, if this task's card can't be built, a plain one that says so with its id."""
    project.task(task_id)  # an unknown id is still an error, not a broken card
    try:
        return _card(project, task_id)
    except Exception:
        log_broken(task_id, "its card")
        line = f"Task {task_id}: {BROKEN}. To see why, run parallax show {task_id} in a terminal; every other task still works."
        return {"task": task_id, "title": f"Task {task_id}", "status": "", "state": "needs you", "cost_usd": 0,
                "report": {"type": "", "bottom": line, "not_looked_at": "", "next": "", "sections": {}},
                "unseen": [], "found": [], "changed": [], "redraft": False, "details": [], "actions": {"kind": "none"},
                "merge": "", "files": [], "live": "", "stages": [], "strip": [], "spend": None, "ask": None,
                "chip": "Can't display", "has_change": False, "shots": [], "docs": [], "broken": True}


def _card(project: Project, task_id: str) -> dict:
    """Everything the decision card needs: the report, and what you can do from it.

    Same facts as `parallax show`, arranged for a decision: the gaps the header could only point to
    come back under Not looked at, and each item's citation is split off so the page can quiet it."""
    t = project.task(task_id)
    report = parse_report(show.report(project, task_id))
    dec = decide.decision(project, task_id)
    files = []
    if dec is not None:
        actions = {"kind": "decide", "question": dec.question, "recommend": dec.recommend,
                   "owner": dec.owner, "why_human": dec.why_human,
                   "options": [{"name": o.name, "does": o.does, "needs_reason": o.needs_reason, "label": o.label}
                               for o in dec.options]}
        files = decide.scope_files(dec.item)
    elif t["status"] == "ready":
        actions = {"kind": "ready"}
    elif t["status"] == "merging":
        actions = {"kind": "merging"}  # no decision while it lands: the buttons wait
    else:
        actions = {"kind": "none"}
    merge, merge_note = "", ""
    if t["status"] == "accepted":
        from .accept import last_stop, merge_command
        accepted = [e for e in project.ledger.entries() if e["kind"] == "task.accepted" and e["data"]["task"] == task_id]
        merge = merge_command(accepted[-1], project) if accepted else ""
        tested = [e for e in project.ledger.entries() if e["kind"] == "merge.tested" and e["data"].get("task") == task_id]
        stop = last_stop(project, task_id)
        target = accepted[-1]["data"].get("target") or "the base branch" if accepted else "the base branch"
        if tested and tested[-1]["data"]["ok"] is False and accepted:
            merge_note = (f"Accepted, but its tests failed on the commit it would land, so {target} didn't move. "
                          f"First failure: {tested[-1]['reason']}. Fix that, then merge by hand:")
        elif stop and stop["data"].get("commands"):
            clash = ", ".join(stop["data"].get("conflict") or []) or "its files"
            merge_note = (f"Accepted, but merging {target} into it hit a conflict in {clash}, so {target} didn't move. "
                          f"Resolve it with these commands in your repo's folder:")
        elif stop:
            merge_note = f"Accepted, but Accept and merge stopped: {stop['reason']}. Run this in your repo's folder:"
    unseen = [report["not_looked_at"]]
    if re.match(r"^see (Found|Details) \(\d+\)$", report["not_looked_at"]):
        where = "Found" if "Found" in report["not_looked_at"] else "Details"
        items = report["sections"].get(where, [])
        gaps = [i for i in items if GAP.search(i)]
        report["sections"][where] = [i for i in items if i not in gaps]
        unseen = [GAP.split(i, 1)[-1] for i in gaps] or unseen
    found = [_cited(i) for i in report["sections"].get("Found", [])
             if not i.startswith(("The work: ", "Heads-up: task "))]  # the title says it; the heads-up shows above
    for f in found:
        f["text"] = re.sub(r"; parallax diff \w+ shows it\.$", ".", f["text"])  # the page has the change a click away
    if files:  # the table under the question says it, file by file
        found = [f for f in found if f["cite"] != f"ledger {dec.item['id']}"]
    kept = {e["id"] for e in project.ledger.entries() if e["data"].get("task") == task_id and e["data"].get("output_sha")}
    details = [_cited(i) for i in report["sections"].get("Details", [])]
    for f in found + details:  # a failure line whose entry kept the whole output links to it
        if f["cite"].startswith("ledger ") and f["cite"][7:] in kept:
            f["output"] = f["cite"][7:]
    state = status.board(t["status"])
    entries = status.attempt(project.ledger.entries(), task_id)
    return {"task": task_id, "title": inbox.title(project, task_id), "status": t["status"], "state": state,
            "cost_usd": round(t.get("cost_usd") or 0, 2), "report": report, "unseen": [_cited(u) for u in unseen],
            "found": found,
            "changed": [_cited(i) for i in report["sections"].get("Changed since last time", [])],
            "redraft": report["bottom"].startswith("Ready again after your reject") or any(
                i.startswith("you rejected the last version") for i in report["sections"].get("Changed since last time", [])),
            "details": details,
            "actions": actions, "merge": merge, "merge_note": merge_note, "files": files,
            "live": live.line(project, task_id) if state in ("drafting", "building", "checking") else "",
            "stages": live.stages(status.attempt(entries, task_id), waiting=state not in ("drafting", "building", "checking")),
            "strip": progress.strip(project, task_id, dec), "spend": progress.spend(project, task_id),
            "ask": {"spent": _ask_spent(project, task_id), "budget": float(project.policy.ask["budget_usd"])},
            "merging": _merging(project, task_id),
            "overlaps": _overlaps(project, task_id),
            "chip": progress.chip(state, dec.kind if dec else ("ready" if t["status"] == "ready" else None))
            if state != "done" else progress.OUTCOMES.get(t["status"], t["status"].capitalize()),
            "has_change": any(e["kind"] == "check.staged" for e in entries) or t["status"] in ("accepted", "merged"),
            "shots": shots(project, task_id),
            "docs": [d for d in ("intent", "spec", "plan", "record") if lifecycle.found_doc(project, task_id, d)]}


def _overlaps(project: Project, task_id: str) -> list[str]:
    from . import overlap
    return [overlap.line(o) for o in overlap.of(project, task_id)]


def _merging(project: Project, task_id: str) -> dict | None:
    from . import merging
    return merging.info(project, task_id)


def _ask_spent(project: Project, task_id: str) -> float:
    from .ask import spent
    return spent(project, task_id)


def output(project: Project, task_id: str, entry_id: str) -> str:
    """The whole output a failing check kept, only after its hash matches the ledger's."""
    from . import outputs
    project.task(task_id)
    for e in project.ledger.entries():
        if e["id"] == entry_id and e["data"].get("task") == task_id:
            return outputs.read(e)
    raise ValueError(f"no ledger entry {entry_id!r} for task {task_id}")


def ledger_entry(project: Project, task_id: str, entry_id: str) -> str:
    """One of this task's ledger entries, as plain text: what a citation on the card points at."""
    import json
    project.task(task_id)
    for e in project.ledger.entries():
        if e["id"] == entry_id and e["data"].get("task") == task_id:
            return json.dumps({k: e[k] for k in ("id", "ts", "kind", "actor", "reason", "data") if k in e}, indent=2)
    raise ValueError(f"no ledger entry {entry_id!r} for task {task_id}")


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
    project.task(task_id)  # a known task only: the id becomes a path below
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
