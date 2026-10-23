"""Two settings in parallax.policy.toml are commands that run as you, outside the sandbox: [build]
setup and [merge] test_command. A cloned repo can ship a policy file, so neither runs until you've
seen them. The first time a task would run one, and again whenever either command changes, the task
stops with one decision: the card shows both commands, and trust runs them. Your answer is recorded
once for the repo (policy.trusted, with the commands and their hash), so a policy whose commands you
approved runs without asking, in every task. The rest of the policy file isn't commands and doesn't
run as you, so changing it (the budget mode, say) asks nothing.
"""
from __future__ import annotations

import hashlib
import json

from .core import POLICY_FILE, ParallaxError, Project

KIND = "policy.trusted"
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
    """You approved exactly this policy's commands, at some point in this repo."""
    want = sha(commands(project.policy))
    return any(e["kind"] == KIND and e["data"].get("commands_sha") == want for e in project.ledger.entries())


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
