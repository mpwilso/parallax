"""docs/tasks/<id>/record.md: the implementation record, generated from the ledger at accept.

Type FYI, in the output shape. Usable during an incident with no digging: what changed, why
(links to the intent, spec and plan), who approved each gate and when, which agent and model
wrote it, what verified it, what it cost, and how to roll it back. The only agent-written part is
"Known risks", taken from the checker and marked as such.
"""
from __future__ import annotations

from . import costs, lifecycle, lint, show
from .core import Project


def approver() -> str:
    import getpass
    import subprocess
    name = subprocess.run(["git", "config", "--get", "user.name"], capture_output=True, text=True).stdout.strip()
    return name or getpass.getuser()


def stamp(ts: str) -> str:
    """2026-09-29T14:20:05+00:00 -> 2026-09-29T14:20Z"""
    return ts[:16] + "Z"


def write(project: Project, task_id: str, files: list[str], plan: dict) -> str:
    entries = [e for e in project.ledger.entries() if e["data"].get("task") == task_id]
    tests = [e for e in entries if e["kind"] == "tests.recorded"]
    verdicts = [e for e in entries if e["kind"] == "verdict.recorded" and e["data"].get("stage") == "check"]
    gates = [e for e in lifecycle.state(project, task_id).approved]
    makers = [e for e in entries if e["kind"] == "maker.finished" and e["data"].get("stage") == "build"]
    risks = [e for e in entries if e["kind"] == "risk.accepted"
             or (e["kind"] == "decision.resolved" and e["data"].get("about") == "disagreement.raised"
                 and e["data"]["outcome"] == "approved")]
    head, findings, gaps = show._checker(verdicts[-1] if verdicts else None)
    base = f"docs/tasks/{task_id}"

    found = show._tests(tests[-1] if tests else None) + head
    found += [f"approved {g['data']['gate'].replace('+', ' and ')} (ledger {g['id']})" for g in gates]
    if makers:
        found.append(f"built by Maker, {makers[-1]['data'].get('model') or 'model not recorded'} (ledger {makers[-1]['id']})")
    found += [f"you accepted a risk: {' '.join(r['reason'].split())} (ledger {r['id']})" for r in risks]

    docs = ", ".join(f"{base}/{d}.md" for d in ("intent", "spec", "plan") if lifecycle.doc_path(project, task_id, d).exists())
    who = approver()
    checker_model = verdicts[-1]["data"].get("model", "") if verdicts else ""
    t = tests[-1]["data"] if tests else {"passed": 0, "total": 0}
    cap, left = costs.budget(project, task_id, plan)
    details = [
        f"Files changed: {', '.join(files) or 'none'}.",
        f"Why: {docs}.",
        *[f"Gate: {g['data']['gate'].replace('+', ' and ')} approved by {who} at {stamp(g['ts'])}, "
          f"signed with the approval key (ledger {g['id']})." for g in gates],
        f"Written by: Maker, which builds in the sandbox ({makers[-1]['data'].get('model') or 'model not recorded'}), in {len(makers)} run"
        f"{'s' if len(makers) != 1 else ''} in the sandbox." if makers else "Written by: nobody recorded.",
        f"Verified by: Parallax ran the plan's tests in the sandbox ({t['passed']} of {t['total']} passed); "
        f"Second Eye, the blind checker ({checker_model or 'model not recorded'}), said {verdicts[-1]['data']['verdict'] if verdicts else 'nothing'}.",
        f"Cost: an estimated ${cap - left:.2f} of the ${cap:.2f} cap.",
        f"Rollback: revert the commit whose message has Parallax-Task: {task_id} "
        f"(find it with git log --grep 'Parallax-Task: {task_id}').",
    ]
    details += [f"Known risks (agent-written, from Second Eye): {f}" for f in findings]

    title = lint.intent_fields(lifecycle.doc_path(project, task_id, "intent").read_text(encoding="utf-8")).get("title", "")
    bottom = lint.one_sentence(f"Task {task_id}, {title}, was accepted as the exact tree Second Eye reviewed")
    if len(bottom.split()) > 20:
        bottom = f"Task {task_id} was accepted as the exact tree Second Eye reviewed."
    text = lint.shaped("FYI", bottom, gaps, "you merge it yourself; nothing else waits on you.", found,
                       who="the checker")
    return _with_details(text, details)


def _with_details(text: str, lines: list[str]) -> str:
    """Put the record's details first under Details, before anything shaped() moved there."""
    block = "\n".join(f"- {d}" for d in lines)
    if "\nDetails\n" in text:
        return text.replace("\nDetails\n", f"\nDetails\n{block}\n", 1) + "\n"
    return f"{text}\nDetails\n{block}\n"
