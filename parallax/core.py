"""Project state: tasks in isolated worktrees, policy checks, and the decision inbox.

State lives in the ledger. The inbox is just items raised and not yet resolved.
"""
from __future__ import annotations

import os
import re
import subprocess
import tomllib
import uuid
from pathlib import Path

from . import status
from .ledger import Ledger
from .mission import MISSION_FILE, TEMPLATE as MISSION_TEMPLATE
from .policy import ALLOW, ASK, DEFAULT_POLICY, DEFAULT_PROFILE, DENY, Policy, normalize_detail

STATE_DIR = ".parallax"
POLICY_FILE = "parallax.policy.toml"
INBOX_KINDS = {"decision.requested", "disagreement.raised", "stuck.raised", "proposal.raised",
               "promotion.raised", "law.raised"}
TASK_ENV, ROOT_ENV = "PARALLAX_TASK", "PARALLAX_ROOT"  # set for every maker process


class ParallaxError(Exception):
    pass


def inside_task(root: Path) -> str | None:
    """The task id if this process was started by a maker working on this project."""
    task, task_root = os.environ.get(TASK_ENV), os.environ.get(ROOT_ENV)
    if not (task and task_root):
        return None
    same = os.path.normcase(str(Path(task_root).resolve())) == os.path.normcase(str(Path(root).resolve()))
    return task if same else None


def refuse_inside_task(root: Path) -> None:
    """Spawn depth 1 and invariant 5: a task can't create tasks or make decisions."""
    if inside_task(root):
        raise ParallaxError("tasks can't create tasks or make decisions")


def _git(repo: Path, *args: str) -> str:
    # git speaks UTF-8; Windows' default (cp1252) crashes on diffs with characters like box drawing
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
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
        self.reload_policy()

    def reload_policy(self) -> None:
        try:
            self.policy = Policy.load(self.root / POLICY_FILE)
        except (ValueError, tomllib.TOMLDecodeError) as err:
            raise ParallaxError(f"bad policy: {err}") from err

    # setup ---------------------------------------------------------------
    @classmethod
    def init(cls, root: Path, actor: str = "human") -> "Project":
        root = Path(root).resolve()
        refuse_inside_task(root)
        _git(root, "rev-parse", "--is-inside-work-tree")
        policy_path = root / POLICY_FILE
        if not policy_path.exists():
            policy_path.write_text(DEFAULT_POLICY)
        if not (root / MISSION_FILE).exists():
            (root / MISSION_FILE).write_text(MISSION_TEMPLATE, encoding="utf-8")
        (root / STATE_DIR).mkdir(exist_ok=True)
        (root / STATE_DIR / ".gitignore").write_text("worktrees/\nruns/\n*.lock\n")
        project = cls(root)
        if not project.ledger.entries():
            project.ledger.append("project.init", actor, "initialized", policy=project.policy.actions)
        return project

    @classmethod
    def find(cls, start: Path) -> "Project":
        for path in [Path(start).resolve(), *Path(start).resolve().parents]:
            if (path / POLICY_FILE).exists() and (path / STATE_DIR).exists():
                return cls(path)
        raise ParallaxError("not a parallax project here. run `parallax init` in your repo's folder")

    # tasks ---------------------------------------------------------------
    def new_task(self, goal: str, actor: str = "human", *, profile: str = DEFAULT_PROFILE,
                 plan: bool = False, queue: bool = False, **refs) -> dict:
        refuse_inside_task(self.root)
        if profile not in self.policy.profiles:
            raise ParallaxError(f"unknown profile {profile!r}")
        task_id = uuid.uuid4().hex[:6]
        branch = f"parallax/{task_id}-{_slug(goal)}"
        worktree = self.state / "worktrees" / task_id
        base = _git(self.root, "rev-parse", "HEAD")
        _git(self.root, "worktree", "add", "-b", branch, str(worktree), base)
        self.ledger.append(
            "task.created", actor, goal,
            task=task_id, branch=branch, worktree=str(worktree), base=base,
            profile=profile, plan=plan, **refs,
        )
        if queue:
            self.queue_task(task_id, actor)
        return self.task(task_id)

    def queue_task(self, task_id: str, actor: str = "human") -> dict:
        refuse_inside_task(self.root)
        t = self.task(task_id)
        if t["status"] in ("queued", "launched", "running", "disputed", "stuck", "closed"):
            raise ParallaxError(f"task {task_id} is {t['status']}, can't queue it")
        return self.ledger.append("task.queued", actor, "", task=task_id)

    def tasks(self) -> dict[str, dict]:
        return status.derive(self.ledger.entries())

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
        t = self.task(task_id)
        profile, key = t["profile"], normalize_detail(action, detail, t["worktree"])
        ruling = self.policy.ruling(action, profile, key)
        refs = {"task": task_id, "action": action, "key": key}
        if ruling == ALLOW:
            e = self.ledger.append("action.granted", actor, detail, **refs)
        elif ruling == DENY:
            listed = self.policy.listed(action, profile, key)
            why = "denied by policy" if listed else "not in policy, denied by default"
            e = self.ledger.append("action.refused", actor, detail, **refs, why=why)
        else:
            e = self.ledger.append("decision.requested", actor, detail, **refs)
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
        refuse_inside_task(self.root)
        if not reason.strip():
            raise ParallaxError("a decision needs a reason")
        pending = {e["id"]: e for e in self.inbox()}
        if decision_id not in pending:
            raise ParallaxError(f"no pending decision {decision_id}")
        req = pending[decision_id]
        context = {k: req["data"][k] for k in ("action", "stage") if k in req["data"]}
        return self.ledger.append(
            "decision.resolved", actor, reason,
            decision=decision_id, task=req["data"].get("task"), about=req["kind"], **context,
            outcome="approved" if approve else "rejected",
        )
