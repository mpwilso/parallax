"""Two settings in parallax.policy.toml are commands that run as you, outside the sandbox: [build]
setup and [merge] test_command. A cloned repo can ship a policy file, so neither runs until you've
seen them. The first time a task would run one, and again whenever either command changes, the task
stops with one decision: the card shows both commands, and trust runs them. Your answer is recorded
once for the repo (policy.trusted, with the commands and their hash), so a policy whose commands you
approved runs without asking, in every task. Tasks already stopped on the same question (a split
request's parts, say) are answered by that one decision: each goes on, and it costs you one touch. The
rest of the policy file isn't commands and doesn't run as you, so changing it (the budget mode, say)
asks nothing.

An eval run (evaltrust.py) asks once for all its cases instead, since each case is a scratch repo with a
new ledger. Your answer reaches each case's ledger as it starts (policy.trusted.run, carried by code,
signed with the approval key) and counts only for that case's exact commands, until the case ends or
the run's time runs out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from . import approvals
from .core import POLICY_FILE, ParallaxError, Project

KIND = "policy.trusted"
RUN_KIND = "policy.trusted.run"    # an eval run's one answer, in one case's scratch ledger (evaltrust.py)
RUN_ENDED = "policy.trust.ended"   # that case ended: its run's trust no longer counts there
SETTINGS = {"setup": "[build] setup", "test_command": "[merge] test_command"}


def commands(policy) -> dict[str, str]:
    """The two commands, as they'd run."""
    return {"setup": policy.build["setup"].strip(), "test_command": policy.merge["test_command"].strip()}


def sha(cmds: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(cmds, sort_keys=True).encode()).hexdigest()


def listed(cmds: dict[str, str]) -> str:
    """Both commands in one line, for the card: [build] setup: ...; [merge] test_command: ..."""
    return "; ".join(f"{SETTINGS[k]}: {cmds[k] or 'none'}" for k in SETTINGS)


def trusted(project: Project) -> bool:
    """You approved exactly this policy's commands, at some point in this repo, or for the eval run
    this scratch repo is a case of, while that case runs."""
    want = sha(commands(project.policy))
    entries = project.ledger.entries()
    return any(e["kind"] == KIND and e["data"].get("commands_sha") == want for e in entries) or for_run(entries, want)


def run_sig(key: bytes, run: str, until: str, cases: dict[str, str]) -> str:
    """The signature on an eval run's trust: the run, when it runs out, and each case's commands hash."""
    return approvals.sign(key, f"eval run {run}", f"trust until {until}", dict(cases))


def for_run(entries: list[dict], want: str) -> bool:
    """An eval run's trust for exactly these commands: signed with the approval key, its case not
    ended, and not run out. Anything else (no key, a bad signature, another case's commands) is no."""
    ended = {e["data"].get("grant") for e in entries if e["kind"] == RUN_ENDED}
    live = [e for e in entries if e["kind"] == RUN_KIND and e["id"] not in ended and e["data"].get("commands_sha") == want]
    key = approvals.key_or_none() if live else None
    if key is None:  # nothing to check, or no key: fails closed
        return False
    now = datetime.now(timezone.utc)
    for e in live:
        d = e["data"]
        try:
            fresh = now < datetime.fromisoformat(d["until"]) and now < datetime.fromisoformat(d["run_until"])
            signed = hmac.compare_digest(run_sig(key, d["run"], d["run_until"], d["cases"]), d["sig"])
            if fresh and signed and d["cases"].get(d["case"]) == want:
                return True
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    return False


class Untrusted(ParallaxError):
    """A command from the policy file would run as you before you've approved it. The question is in
    the inbox by the time this is raised."""


def check(project: Project, task_id: str, what: str) -> None:
    """Before `what` (the setting's name) runs as you. Trusted: returns. Otherwise the task gets one
    decision, a stuck item carrying both commands and their hash, asked once, and this raises."""
    if trusted(project):
        return
    cmds = commands(project.policy)
    want = sha(cmds)
    why = f"{what} runs as you, and you haven't approved this policy's commands yet: {listed(cmds)}"
    if not any(e["data"].get("task") == task_id and e["data"].get("commands_sha") == want for e in project.inbox()):
        policy_sha = hashlib.sha256((project.root / POLICY_FILE).read_bytes()).hexdigest()
        project.ledger.append("stuck.raised", "parallax", why, task=task_id, commands_sha=want, policy_sha=policy_sha, **cmds)
    raise Untrusted(why)


def approve(project: Project, reason: str, item: dict | None = None) -> dict:
    """Your trust, recorded for the repo: exactly the commands the card showed you (the item's), or the
    policy's current ones. A command that changes afterwards asks again."""
    cmds = {k: str(item["data"].get(k) or "") for k in SETTINGS} if item else commands(project.policy)
    extra = {"task": item["data"].get("task"), "decision": item["id"]} if item else {}
    return project.ledger.append(KIND, "human", reason, commands_sha=sha(cmds), **cmds, **extra)


def answer_the_rest(project: Project, item: dict, answer: dict) -> list[str]:
    """Your trust on one card (answer, its decision.resolved entry), carried to every other open card
    that asked about exactly the same commands: each is resolved by code, citing your decision, so it
    isn't a touch of yours. Returns their tasks. A card about other commands still waits for you."""
    want, asked_by = item["data"].get("commands_sha"), item["data"].get("task")
    said = f"answered by your trust on task {asked_by} (decision {answer['id']}): the same commands"
    out = []
    for e in project.inbox():
        if want and e["kind"] == "stuck.raised" and e["data"].get("commands_sha") == want:
            project.resolve(e["id"], True, said, actor="parallax", by=answer["id"])
            tid = e["data"].get("task")
            if tid and tid != asked_by and tid not in out:
                out.append(tid)
    return out
