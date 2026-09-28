"""`parallax show <task>`: where a task stands, in the output shape, from the ledger.

Type comes from the task's state, never from an agent: a pending gate, Ready, or an item in your
inbox is Decision needed; work in progress or a finished task is FYI. Every Found item cites the
ledger entry it comes from. The checker's own Not looked at is carried, never replaced.
"""
from __future__ import annotations

from pathlib import Path

from . import lifecycle, lint, tree
from .core import Project


def _last(entries: list[dict], kind: str, **match) -> dict | None:
    hits = [e for e in entries if e["kind"] == kind and all(e["data"].get(k) == v for k, v in match.items())]
    return hits[-1] if hits else None


def _tests(e: dict | None) -> list[str]:
    if not e or e["data"]["exit"] not in (0, 1):  # they didn't run: the reason says why, counts would mislead
        return []
    out = []
    for f, (passed, counted, skipped) in e["data"]["per_file"].items():
        more = f", {skipped} skipped" if skipped else ""
        out.append(f"{f}: {passed} of {counted} passed{more} (ledger {e['id']})")
    return out


def _checker(v: dict | None) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(the verdict line, one line per finding, the checker's Not looked at as a gap)."""
    if not v:
        return [], [], []
    d, cite = v["data"], f"(ledger {v['id']})"
    n = len(d.get("findings", []))
    head = f"checker: {d['verdict']}, {'no findings' if not n else f'{n} finding' + ('s' if n > 1 else '')} {cite}"
    findings = [f"{f['where'] or 'the change'} {f['severity']}: {' '.join(f['text'].split())} {cite}"
                for f in d.get("findings", [])]
    nla = " ".join(str(d.get("not_looked_at") or "nothing").split())
    gaps = [] if nla.rstrip(".").lower() == "nothing" else [(f"the checker says: {nla}", f"checker did not look at: {nla} {cite}")]
    return [head], findings, gaps


def _changed(project: Project, task_id: str, entries: list[dict]) -> list[str]:
    """After rework: which files each cycle changed. Only on a report after rework."""
    staged = [e for e in entries if e["kind"] == "check.staged"]
    reworks = [e for e in entries if e["kind"] == "rework.started"]
    if not reworks or len(staged) < 2:
        return []
    wt = Path(project.task(task_id)["worktree"])
    out = []
    for n, (old, new) in enumerate(zip(staged, staged[1:]), start=1):
        files = tree.changed_between(wt, old["data"]["tree"], new["data"]["tree"])
        out.append(f"rework {n} changed {', '.join(files) or 'nothing'} (ledger {new['id']})")
    return out


def report(project: Project, task_id: str) -> str:
    t = project.task(task_id)
    if not t.get("intent"):
        return lint.report("FYI", lint.one_sentence(f"Task {task_id} is {t['status']}"), "nothing",
                           "see it with parallax task diff " + task_id + ".")
    st = lifecycle.state(project, task_id)
    if st.gate is not None:
        return lifecycle.report(project, task_id)

    entries = [e for e in project.ledger.entries() if e["data"].get("task") == task_id]
    status = t["status"]
    tests = _last(entries, "tests.recorded")
    verdict = _last(entries, "verdict.recorded", stage="check")
    staged = _last(entries, "check.staged")
    head, findings, gaps = _checker(verdict)
    found = _tests(tests) + head
    boundary = [f"boundary change: {f} runs automatically (ledger {staged['id']})"
                for f in (staged["data"]["autorun"] if staged else [])]
    changed = _changed(project, task_id, entries)
    open_items = [e for e in project.inbox() if e["data"].get("task") == task_id]

    if open_items:  # the short label goes up top; the whole reason goes under Found, cited
        item = open_items[-1]
        why = " ".join(item["reason"].split())
        bottom = lint.one_sentence(f"Needs you: {why.split(':')[0].split(';')[0]}")
        if len(bottom.split()) > 15:
            bottom = "Needs you: an item is waiting in your inbox."
        return lint.shaped("Decision needed", bottom, gaps,
                           "you approve (accept the risk) or reject it in parallax inbox, with a reason.", [f"{why} (ledger {item['id']})"] + found, changed, "the checker",
                           boundary + findings)
    if status == "ready":
        passed = tests["data"]["passed"] if tests else 0
        total = tests["data"]["total"] if tests else 0
        how = "passed" if verdict and verdict["data"]["verdict"] == "pass" else "found nothing blocking"
        if verdict and verdict["data"]["verdict"] in ("error", "fail"):
            how = f"said {verdict['data']['verdict']} and you accepted the risk"
        bottom = f"Ready: the checker {how} and {passed} of {total} plan tests pass."
        return lint.shaped("Decision needed", bottom, gaps, f"you run parallax accept {task_id}, or reject it with a reason.",
                           found, changed, "the checker", boundary + findings)
    if status in ("accepted", "merged"):
        from .accept import merge_command
        acc = _last(entries, "task.accepted")
        if status == "merged":
            return lint.report("FYI", f"Task {task_id} was accepted as {acc['data']['commit'][:7]} and you merged it unchanged.",
                               "nothing", "nothing waits on you.", [f"merge confirmed (ledger {_last(entries, 'merge.confirmed')['id']})"])
        return lint.report("Decision needed", f"Task {task_id} was accepted as {acc['data']['commit'][:7]}; merging is yours.",
                           "nothing", f"you run {merge_command(acc)}.", [f"accepted (ledger {acc['id']})"])
    if status in ("running", "checking", "reworking"):
        cycles = sum(e["kind"] == "rework.started" for e in entries)
        more = f" (rework {cycles} of {project.policy.check['rework_cap']})" if cycles else ""
        return lint.report("FYI", f"Task {task_id} is {status}{more}.", "nothing",
                           "nothing waits on you; parallax stop ends it.")
    if status in ("built", "risk accepted"):
        return lint.report("Decision needed" if status == "risk accepted" else "FYI",
                           f"Task {task_id} is {status} and not checked yet.", "nothing",
                           f"you run parallax recheck {task_id}.")
    if status == "needs work":
        return lint.shaped("Decision needed", "The check sent it back and you agreed.", gaps,
                           f"you run parallax build {task_id} again, or reject it with a reason.", found, changed,
                           "the checker", boundary + findings)
    return lint.shaped("FYI", lint.one_sentence(f"Task {task_id} is {status}"), gaps, "nothing waits on you.",
                       found, changed, "the checker", boundary + findings)
