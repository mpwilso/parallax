"""`parallax show <task>`: where a task stands, in the output shape, from the ledger.

Type comes from the task's state, never from an agent: a pending gate, Ready, or an item in your
inbox is Decision needed; work in progress or a finished task is FYI. Every Found item cites the
ledger entry it comes from. The checker's own Not looked at is carried, never replaced.
"""
from __future__ import annotations

from pathlib import Path

from . import costs, decide, lifecycle, lint, since, tree
from .status import attempt as _attempt
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
    line = (f"All {d['total']} plan tests passed" if d["passed"] == d["total"] and d["total"]
            else f"{d['passed']} of {d['total']} plan tests passed")
    line += f", and {skipped} were skipped" if skipped else ""
    if not failing:
        return [f"{line}. {cite}"]
    names = [f.rsplit("/", 1)[-1].removesuffix(".py") for f in failing]
    more = f" and {len(names) - 5} more" if len(names) > 5 else ""
    out = [f"{line}. Failing: {', '.join(names[:5])}{more}. {cite}"]
    changed = set(staged["data"]["files"]) if staged else set()
    touched = [f for f in failing if f in changed]
    out.append(f"This change edited the failing test files {', '.join(touched)}. {cite}" if touched
               else f"This change didn't edit any failing test file, so they may fail without it too. {cite}")
    return out


def _checker(v: dict | None) -> tuple[list[str], list[str], list[tuple[str, str]]]:
    """(the verdict line, one line per finding, the checker's Not looked at as a gap)."""
    if not v:
        return [], [], []
    d, cite = v["data"], f"(ledger {v['id']})"
    n = len(d.get("findings", []))
    said = {"pass": "passed it", "fail": "failed it", "no_finding": "found nothing wrong, but couldn't confirm the outcome",
            "error": "gave no usable answer"}.get(d["verdict"], d["verdict"])
    points = "" if not n else f", with {n} point" + ("s" if n > 1 else "") + " below"
    head = f"Second Eye, the blind checker, {said}{points}. {cite}"
    findings = [f"{f['severity'].capitalize()}, at {f['where'] or 'the change as a whole'}: "
                f"{' '.join(f['text'].split()).rstrip('.')}. {cite}" for f in d.get("findings", [])]
    nla = " ".join(str(d.get("not_looked_at") or "nothing").split())
    gaps = [] if nla.rstrip(".").lower() == "nothing" else [(f"Second Eye says: {nla}", f"Second Eye didn't check: {nla} {cite}")]
    return [head], findings, gaps


def _ui(entries: list[dict]) -> tuple[list[str], list[tuple[str, str]]]:
    """The UI tester's flows in one line, and what it says it didn't look at, for this attempt."""
    rec, ran = _last(entries, "uitest.recorded"), _last(entries, "flows.recorded")
    found, gaps = [], []
    if ran:
        d, cite = ran["data"], f"(ledger {ran['id']})"
        if d.get("app_failed"):
            found.append(f"The app didn't start for the UI flow tests. {cite}")
        else:
            bad = [c["name"] for c in d.get("failed") or []]
            found.append(f"{d['passed']} of {d['total']} UI flow tests passed" + (f". Failing: {', '.join(bad[:3])}" if bad else "")
                         + f". {cite}")
    if rec:
        for why in rec["data"].get("dropped") or []:
            found.append(f"Field's test was dropped: {why.rstrip('.')}. (ledger {rec['id']})")
        nla = " ".join(str(rec["data"].get("not_looked_at") or "nothing").split())
        if nla.rstrip(".").lower() != "nothing":
            gaps.append((f"Field says: {nla}", f"Field didn't check: {nla} (ledger {rec['id']})"))
    return found, gaps


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
        out.append(f"Rework {n} changed {', '.join(files) or 'nothing'}. (ledger {new['id']})")
    return out


def _the_work(project: Project, task_id: str, staged: dict | None) -> list[str]:
    """What the work was, and what changed: the first two lines of a Ready card."""
    intent = lifecycle._read(project, task_id, "intent")
    what = lint.intent_fields(intent).get("title") or project.task(task_id)["goal"]
    out = [f"The work: {' '.join(what.split())}. (docs/tasks/{task_id}/intent.md:1)"]
    if staged:
        files = ", ".join(staged["data"]["files"]) or "nothing"
        n = staged["data"]["lines"]
        out.append(f"It changes {files}, {n} line{'' if n == 1 else 's'}; parallax diff {task_id} shows it. (ledger {staged['id']})")
    return out


def _outcomes(project: Project, task_id: str, tests: dict | None) -> list[str]:
    """One line per outcome in the intent: the test files that ran for it, through the plan's covers,
    or that no test exercises it. Code only: the covers map against the recorded JUnit counts."""
    intent = lifecycle._read(project, task_id, "intent")
    plan = lifecycle.plan_data(project, task_id) or {}
    ran = {f for f, (passed, counted, _) in (tests["data"]["per_file"].items() if tests else []) if counted}
    covers = {str(k): v for k, v in plan.get("covers", {}).items()}
    cite = f"(ledger {tests['id']})" if tests else f"(docs/tasks/{task_id}/plan.md:1)"
    out = []
    said = _reticle(project, task_id)
    kinds = lint.outcome_kinds(intent)
    for n in lint.outcomes_of(intent):
        files = sorted({c.split("::", 1)[0] for c in covers.get(n, []) if c.split("::", 1)[0] in ran})
        whose = {"asked": ", which you asked for", "inferred": ", which Focus added"}.get(kinds.get(n) or "", "")
        line = f"Outcome {n}{whose}: tested by {', '.join(files)}" if files else f"Outcome {n}{whose}: no test covers it"
        extra = said(n)  # Reticle's words, with its own ledger id last
        text, own = (extra.rsplit(" (ledger ", 1) + [""])[:2] if " (ledger " in extra else (extra, "")
        out.append(f"{line}{text}." + (f" (ledger {own}" if own else "") + f" {cite}")
    return out


def _reticle(project: Project, task_id: str):
    """outcome -> "; Reticle: ..." for this attempt: its test passed or failed, or why there's none."""
    from . import reticle
    rec = reticle.recorded(project, task_id)
    if rec is None:
        return lambda n: ""
    ran = _last(_attempt([e for e in project.ledger.entries() if e["data"].get("task") == task_id], task_id), "reticle.ran")
    kept = reticle.kept(project, task_id)
    failed = {t["outcome"]: t["message"] for t in (ran["data"]["failed"] if ran else [])}

    def said(n: str) -> str:
        cite = f" (ledger {(ran or rec)['id']})"
        if rec["kind"] == "reticle.failed":  # Parallax's own reason when it has one, like no asked outcome
            why = lint.one_sentence(rec["reason"]).rstrip(".") if rec["actor"] == "parallax" else "it failed"
            return f"; Reticle wrote no test: {why}{cite}"
        if any(t["outcome"] == n for t in kept):
            if n in failed:
                return f"; Reticle's test failed: {' '.join(failed[n].split())[:80]}{cite}"
            return f"; Reticle's test passed{cite}" if ran else f"; Reticle's test hasn't run{cite}"
        mine = [w["why"] for w in rec["data"].get("weak") or [] if w.get("name", "").startswith(f"test_outcome_{n}_")]
        whole = [w["why"] for w in rec["data"].get("weak") or [] if not reticle.NAME.match(w.get("name", ""))]
        why = (mine or ([] if kept else whole) or ["it wrote none for this outcome"])[0]
        return f"; no Reticle test: {' '.join(why.split())[:80]}{cite}"
    return said


def _rails(entries: list[dict], tests: dict | None) -> list[str]:
    """Whether preflight passed before the build, and which harness files the check put back."""
    out = []
    pf = _last(entries, "preflight.recorded")
    if pf:
        out.append(f"The sandbox check before the build {'passed' if pf['data'].get('ok') else 'failed'}. (ledger {pf['id']})")
    if tests:
        reset = tests["data"].get("harness_reset") or []
        out.append(f"Maker changed how the tests run ({', '.join(reset)}), so the check put those files back first. "
                   f"(ledger {tests['id']})" if reset else f"Maker didn't change how the tests run. (ledger {tests['id']})")
    return out


def _next(task_id: str, dec) -> str:
    names = [o.name for o in dec.options]
    return f"you run parallax decide {task_id} with {', '.join(names[:-1])} or {names[-1]}."


def lead(why: str) -> str:
    """The first clause of a reason, up to 16 words: "error: the sandbox exited (srt: ...)" leads
    with "the sandbox exited", never with "error"."""
    for generic in ("error: ", "Second Eye error: "):
        if why.lower().startswith(generic):
            why = why[len(generic):]
    first = why.split(": ", 1)[0].split("; ", 1)[0]
    if first.count("(") > first.count(")"):
        first = first.rsplit(" (", 1)[0]
    return " ".join(first.split()[:16])


def _decision(project: Project, task_id: str, dec, found: list[str], gaps: list[tuple[str, str]],
              changed: list[str] = (), extra: list[str] = ()) -> str:
    """One Decision needed: the problem up top, the question and its options, then the evidence."""
    options = [f"{o.name}: {o.does}" + (" (needs a reason)" if o.needs_reason else "") for o in dec.options]
    if dec.item:
        why = " ".join(dec.item["reason"].split())
        bottom = lint.one_sentence("Needs you: " + lead(why))  # the real problem, never a placeholder
        cite = f"(ledger {dec.item['id']})"
        hint = decide.sandbox_hint(why) if dec.kind == "error" else []  # how to find a sandbox that won't start
        found = [f"{why} {cite}"] + [f"{h} {cite}" for h in hint] + list(found)
    else:
        bottom = "The plan waits for you before it runs."
        plan = lifecycle.plan_data(project, task_id) or {}
        cap = costs.budget(project, task_id, plan)[0] if plan else 0.0
        found = _the_work(project, task_id, None) + [
            f"It's estimated at ${float(plan.get('estimated_cost_usd', 0)):.2f}, with a cap of ${cap:.2f}. (docs/tasks/{task_id}/plan.md:1)",
            f"It waits because {dec.extra.get('why', '').rstrip('.')}."
            + (f" (ledger {dec.extra['asked']})" if dec.extra.get("asked") else " (Unverified)")]
        gaps = [(f"{doc}.md says: {text}", f"docs/tasks/{task_id}/{doc}.md:{n} not looked at: {text}")
                for doc, n, text in lifecycle.gaps(project, task_id, ("intent", "plan"))]
    who = "the checker" if dec.item else "the drafters"  # a plan under review hasn't met the checker yet
    return lint.shaped("Decision needed", bottom, gaps, _next(task_id, dec), found, changed, who,
                       list(extra), decisions=decide.lines(dec), details=options + _files(dec))


def _files(dec) -> list[str]:
    """Every file behind a grouped scope problem, for Details: the card's lead line only counts them."""
    files = decide.scope_files(dec.item)
    if len(files) < 2:
        return []

    def size(n):
        return "deleted or not a file" if n is None else ("empty" if n == 0 else f"{n} bytes")
    why = {"protected": "protected path", "outside": "not in the plan", "binary": "unlisted binary",
           "symlink": "unlisted symlink", "dependency": "unlisted dependency"}
    return [f"{f['path']}: {why.get(f['cause'], f['cause'])}, {size(f['size'])}" for f in files]


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
    ui_found, ui_gaps = _ui(_attempt(entries, task_id))
    gaps = gaps + ui_gaps
    found = _tests(tests, staged) + ui_found + head
    boundary = [f"{f} runs automatically once merged, so read it before you accept. (ledger {staged['id']})"
                for f in (staged["data"]["autorun"] if staged else [])]
    lead = since.lines(project, task_id, entries)  # after your reject, what changed comes first
    changed = lead + _changed(project, task_id, _attempt(entries, task_id))
    if dec is not None:
        if dec.item and not _last(_attempt(entries, task_id), "verdict.recorded", stage="check"):  # it stopped before the checker: say so, never "nothing"
            gaps = gaps + [("the plan's tests and Second Eye, which haven't run",
                            f"not looked at: the plan's tests and Second Eye (the blind checker) haven't run (ledger {dec.item['id']})")]
        return _decision(project, task_id, dec, found, gaps, changed, boundary + findings)
    if status == "ready":
        found = _the_work(project, task_id, staged) + found + _outcomes(project, task_id, tests)
        boundary = boundary + _rails(_attempt(entries, task_id), tests)
        passed = tests["data"]["passed"] if tests else 0
        total = tests["data"]["total"] if tests else 0
        how = "passed" if verdict and verdict["data"]["verdict"] == "pass" else "found nothing blocking"
        if verdict and verdict["data"]["verdict"] in ("error", "fail"):
            how = f"said {verdict['data']['verdict']} and you accepted the risk"
        bottom = f"Ready: Second Eye {how} and {passed} of {total} plan tests pass."
        if lead:
            bottom = f"Ready again after your reject: Second Eye {how} and {passed} of {total} plan tests pass."
        disputed = _last(_attempt(entries, task_id), "reticle.disputed")
        if disputed and staged and disputed["data"]["tree"] == staged["data"]["tree"]:  # Reticle disagrees: yours to judge
            outs = sorted({t["outcome"] for t in disputed["data"]["failed"]})
            bottom = (f"Ready, but Reticle disagrees: its test of outcome {', '.join(outs)} still fails, while Second Eye "
                      f"{how} and {passed} of {total} plan tests pass.")
            found = [f"Reticle, outcome {t['outcome']}: its test still fails after one rework: "
                     f"{' '.join(t['message'].split())[:120]} (ledger {disputed['id']})"
                     for t in disputed["data"]["failed"]] + found
        return lint.shaped("Decision needed", bottom, gaps, f"you run parallax accept {task_id}, or reject it with a reason.",
                           found, changed, "the checker", boundary + findings)
    if status in ("accepted", "merged"):
        from .accept import merge_command
        acc = _last(entries, "task.accepted")
        tested = _last(entries, "merge.tested")
        ran = [] if not tested else [
            f"Its tests passed on that commit before the merge ({tested['data']['command']}). (ledger {tested['id']})"
            if tested["data"]["ok"] else
            f"No test command is configured ([merge] test_command), so it merged without a test run. (ledger {tested['id']})"
            if tested["data"]["ok"] is None else
            f"Its tests failed on that commit: {tested['reason'].rstrip('.')}. (ledger {tested['id']})"]
        if status == "merged":
            return lint.report("FYI", f"Task {task_id} was accepted as {acc['data']['commit'][:7]} and you merged it unchanged.",
                               "nothing", "nothing waits on you.",
                               [f"merge confirmed (ledger {_last(entries, 'merge.confirmed')['id']})"] + ran)
        if tested and tested["data"]["ok"] is False:
            return lint.report("Decision needed", lint.one_sentence(
                f"Task {task_id} was accepted as {acc['data']['commit'][:7]}, but its tests failed before the merge, "
                f"so {acc['data'].get('target') or 'the base branch'} didn't move"),
                "nothing", f"you fix that, then run {merge_command(acc)}.", [f"accepted (ledger {acc['id']})"] + ran)
        return lint.report("Decision needed", f"Task {task_id} was accepted as {acc['data']['commit'][:7]}; merging is yours.",
                           "nothing", f"you run {merge_command(acc)}.", [f"accepted (ledger {acc['id']})"] + ran)
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
