"""Project state: tasks in isolated worktrees, policy checks, and the decision inbox.

State lives in the ledger. The inbox is just decisions requested and not yet resolved.
"""
from __future__ import annotations

import re
import subprocess
import uuid
from pathlib import Path

from .agents.base import AGREE
from .ledger import Ledger
from .policy import ALLOW, ASK, DEFAULT_POLICY, DENY, Policy

STATE_DIR = ".parallax"
POLICY_FILE = "parallax.policy.toml"
INBOX_KINDS = {"decision.requested", "disagreement.raised"}


class ParallaxError(Exception):
    pass


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if result.returncode != 0:
        raise ParallaxError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout.strip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:30] or "task"


class Project:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state = self.root / STATE_DIR
        self.ledger = Ledger(self.state / "ledger.jsonl")
        self.policy = Policy.load(self.root / POLICY_FILE)

    # setup ---------------------------------------------------------------
    @classmethod
    def init(cls, root: Path, actor: str = "human") -> "Project":
        root = Path(root).resolve()
        _git(root, "rev-parse", "--is-inside-work-tree")
        policy_path = root / POLICY_FILE
        if not policy_path.exists():
            policy_path.write_text(DEFAULT_POLICY)
        (root / STATE_DIR).mkdir(exist_ok=True)
        (root / STATE_DIR / ".gitignore").write_text("worktrees/\n")
        project = cls(root)
        if not project.ledger.entries():
            project.ledger.append("project.init", actor, "initialized", policy=project.policy.actions)
        return project

    @classmethod
    def find(cls, start: Path) -> "Project":
        for path in [Path(start).resolve(), *Path(start).resolve().parents]:
            if (path / POLICY_FILE).exists() and (path / STATE_DIR).exists():
                return cls(path)
        raise ParallaxError("not a parallax project (run `parallax init`)")

    # tasks ---------------------------------------------------------------
    def new_task(self, goal: str, actor: str = "human") -> dict:
        task_id = uuid.uuid4().hex[:6]
        branch = f"parallax/{task_id}-{_slug(goal)}"
        worktree = self.state / "worktrees" / task_id
        base = _git(self.root, "rev-parse", "HEAD")
        _git(self.root, "worktree", "add", "-b", branch, str(worktree), base)
        self.ledger.append(
            "task.created", actor, goal,
            task=task_id, branch=branch, worktree=str(worktree), base=base,
        )
        return self.task(task_id)

    def tasks(self) -> dict[str, dict]:
        """Task status is derived from the ledger, never stored separately."""
        out: dict[str, dict] = {}
        for e in self.ledger.entries():
            kind, d = e["kind"], e["data"]
            if kind == "task.created":
                out[d["task"]] = {"goal": e["reason"], "status": "open", **d}
                continue
            t = out.get(d.get("task"))
            if t is None:
                continue
            if kind == "task.closed":
                t["status"] = d["outcome"]
            elif kind == "maker.started":
                t["status"] = "running"
            elif kind == "maker.finished" and d["status"] != "done":
                t["status"] = "maker failed"
            elif kind == "verdict.recorded" and d["stage"] == "diff" and d["verdict"] in AGREE:
                t["status"] = "ready"
            elif kind == "disagreement.raised":
                t["status"] = "disputed"
            elif kind == "decision.resolved" and d.get("about") == "disagreement.raised":
                if d["outcome"] == "rejected":
                    t["status"] = "needs work"
                else:
                    t["status"] = "plan approved" if d.get("stage") == "plan" else "ready"
        return out

    def task(self, task_id: str) -> dict:
        tasks = self.tasks()
        if task_id not in tasks:
            raise ParallaxError(f"no task {task_id}")
        return tasks[task_id]

    def diff(self, task_id: str, *args: str) -> str:
        """Working tree against the task's base. Content only, never commit messages."""
        t = self.task(task_id)
        wt = Path(t["worktree"])
        _git(wt, "add", "-N", ".")  # include new files in the diff without staging content
        return _git(wt, "diff", *args, t["base"])

    # policy checks ---------------------------------------------------------
    def check(self, task_id: str, action: str, detail: str = "", actor: str = "agent") -> dict:
        """An agent asks to take an action. Returns the ruling and logs it."""
        self.task(task_id)
        ruling = self.policy.ruling(action)
        if ruling == ALLOW:
            e = self.ledger.append("action.granted", actor, detail, task=task_id, action=action)
        elif ruling == DENY:
            listed = action in self.policy.actions
            why = "denied by policy" if listed else "not in policy, denied by default"
            e = self.ledger.append("action.refused", actor, detail, task=task_id, action=action, why=why)
        else:
            e = self.ledger.append("decision.requested", actor, detail, task=task_id, action=action)
        return {"ruling": ruling, "entry": e}

    # inbox -----------------------------------------------------------------
    def inbox(self) -> list[dict]:
        """Permission requests and maker/checker disagreements nobody has ruled on yet."""
        entries = self.ledger.entries()
        resolved = {e["data"]["decision"] for e in entries if e["kind"] == "decision.resolved"}
        return [e for e in entries if e["kind"] in INBOX_KINDS and e["id"] not in resolved]

    def decision_outcome(self, decision_id: str) -> dict | None:
        """The human's ruling on an inbox item, or None while it's still pending."""
        for e in self.ledger.entries():
            if e["kind"] == "decision.resolved" and e["data"]["decision"] == decision_id:
                return e
        return None

    def resolve(self, decision_id: str, approve: bool, reason: str, actor: str = "human") -> dict:
        """For a disagreement, approve sides with the maker and reject sides with the checker."""
        if not reason.strip():
            raise ParallaxError("a decision needs a reason")
        pending = {e["id"]: e for e in self.inbox()}
        if decision_id not in pending:
            raise ParallaxError(f"no pending decision {decision_id}")
        req = pending[decision_id]
        context = {k: req["data"][k] for k in ("action", "stage") if k in req["data"]}
        return self.ledger.append(
            "decision.resolved", actor, reason,
            decision=decision_id, task=req["data"]["task"], about=req["kind"], **context,
            outcome="approved" if approve else "rejected",
        )
