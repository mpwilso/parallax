"""`parallax show <task>`: where a task stands, in the output shape, from the ledger.

Type comes from the task's state, never from an agent: a pending gate, Ready, or an item in your
inbox is Decision needed; work in progress or a finished task is FYI. Every Found item cites the
ledger entry it comes from. The checker's own Not looked at is carried, never replaced.
"""
from __future__ import annotations

from pathlib import Path

from . import costs, decide, lifecycle, lint, tree
from .core import Project


def _last(entries: list[dict], kind: str, **match) -> dict | None:
    hits = [e for e in entries if e["kind"] == kind and all(e["data"].get(k) == v for k, v in match.items())]
    return hits[-1] if hits else None


def _tests(e: dict | None, staged: dict | None = None) -> list[str]:
    """The tests in one line: how many passed, and only the files that failed.

    When some fail, a second line says whether any failing test file is one the diff changed:
    if none is, the failures may not be this change's doing."""
    if not e or e["data"]["exit"] not in (0, 1):  # they didn't run: the reason says why, counts would mislead
        return []
    d, cite = e["data"], f"(ledger {e['id']})"
    failing = [f for f, (passed, counted, _) in d["per_file"].items() if passed < counted]
    skipped = sum(v[2] for v in d["per_file"].values())
    line = f"tests: {d['passed']} of {d['total']} passed"
    line += f", {skipped} skipped" if skipped else ""
    if not failing:
        return [f"{line} {cite}"]
    names = [f.rsplit("/", 1)[-1].removesuffix(".py") for f in failing]
    more = f" and {len(names) - 5} more" if len(names) > 5 else ""
    out = [f"{line}; failures in {', '.join(names[:5])}{more} {cite}"]
    changed = set(staged["data"]["files"]) if staged else set()
    touched = [f for f in failing if f in changed]
    out.append(f"failing test files the diff changed: {', '.join(touched)} {cite}" if touched
               else f"no failing test file is one the diff changed; they may fail without this change too {cite}")
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


def _the_work(project: Project, task_id: str, staged: dict | None) -> list[str]:
    """What the work was, and what changed: the first two lines of a Ready card."""
    intent = lifecycle._read(project, task_id, "intent")
    what = lint.intent_fields(intent).get("title") or project.task(task_id)["goal"]
    out = [f"the work: {' '.join(what.split())} (docs/tasks/{task_id}/intent.md:1)"]
    if staged:
        files = ", ".join(staged["data"]["files"]) or "nothing"
        out.append(f"changed: {files}, {staged['data']['lines']} line{'' if staged['data']['lines'] == 1 else 's'}; parallax diff {task_id} shows it (ledger {staged['id']})")
    return out


def _next(task_id: str, dec) -> str:
    names = [o.name for o in dec.options]
    return f"you run parallax decide {task_id} with {', '.join(names[:-1])} or {names[-1]}."


def _decision(project: Project, task_id: str, dec, found: list[str], gaps: list[tuple[str, str]],
              changed: list[str] = (), extra: list[str] = ()) -> str:
    """One Decision needed: the problem up top, the question and its options, then the evidence."""
    options = [f"{o.name}: {o.does}" + (" (needs a reason)" if o.needs_reason else "") for o in dec.options]
    if dec.item:
        why = " ".join(dec.item["reason"].split())
        lead = why.split(": ", 1)[0].split("; ", 1)[0].split()
        bottom = lint.one_sentence("Needs you: " + " ".join(lead[:16]))  # the real problem, never a placeholder
        found = [f"{why} (ledger {dec.item['id']})"] + list(found)
    else:
        bottom = "The plan waits for you before it runs."
        plan = lifecycle.plan_data(project, task_id) or {}
        cap = costs.budget(project, task_id, plan)[0] if plan else 0.0
        found = _the_work(project, task_id, None) + [
            f"cost: estimated ${float(plan.get('estimated_cost_usd', 0)):.2f}, cap ${cap:.2f} (docs/tasks/{task_id}/plan.md:1)",
            f"why it waits: {dec.extra.get('why', '')}"
            + (f" (ledger {dec.extra['asked']})" if dec.extra.get("asked") else " (Unverified)")]
        gaps = [(f"{doc}.md says: {text}", f"docs/tasks/{task_id}/{doc}.md:{n} not looked at: {text}")
                for doc, n, text in lifecycle.gaps(project, task_id, ("intent", "plan"))]
    who = "the checker" if dec.item else "the drafters"  # a plan under review hasn't met the checker yet
    return lint.shaped("Decision needed", bottom, gaps, _next(task_id, dec), found, changed, who,
                       list(extra), decisions=[decide.line(dec)], details=options)


def report(project: Project, task_id: str) -> str:
    t = project.task(task_id)
    if not t.get("intent"):
        return lint.report("FYI", lint.one_sentence(f"Task {task_id} is {t['status']}"), "nothing",
                           "see it with parallax task diff " + task_id + ".")
    st = lifecycle.state(project, task_id)
    entries = [e for e in project.ledger.entries() if e["data"].get("task") == task_id]
    status = t["status"]
    dec = decide.decision(project, task_id)
    if st.gate is not None and dec is None:
        if status == "drafting":
            return lint.report("FYI", f"Task {task_id} is drafting.", "nothing", "nothing waits on you; parallax stop ends it.")
        return lifecycle.report(project, task_id)
    if dec is not None and dec.item is None:
        return _decision(project, task_id, dec, [], [])

    tests = _last(entries, "tests.recorded")
    verdict = _last(entries, "verdict.recorded", stage="check")
    staged = _last(entries, "check.staged")
    head, findings, gaps = _checker(verdict)
    found = _tests(tests, staged) + head
    boundary = [f"boundary change: {f} runs automatically (ledger {staged['id']})"
                for f in (staged["data"]["autorun"] if staged else [])]
    changed = _changed(project, task_id, entries)
    if dec is not None:
        return _decision(project, task_id, dec, found, gaps, changed, boundary + findings)
    if status == "ready":
        found = _the_work(project, task_id, staged) + found
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
    if status in ("drafting", "running", "checking", "reworking"):
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
