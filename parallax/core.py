"""Project state: tasks in isolated worktrees, policy checks, and the decision inbox.

State lives in the ledger. The inbox is just items raised and not yet resolved.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import tomllib
import uuid
from pathlib import Path

from . import status
from .ledger import Ledger
from .policy import DEFAULT_POLICY, Policy

STATE_DIR = ".parallax"
POLICY_FILE = "parallax.policy.toml"
EXAMPLE_POLICY_FILE = "parallax.policy.example.toml"
INBOX_KINDS = {"disagreement.raised", "stuck.raised"}
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


GIT_WRONG = ("fatal:", "error:")


def git_failed(args, stderr: str | bytes) -> ParallaxError:
    """A failed git command, said with the command and git's own error. git writes progress lines such
    as "Preparing worktree (new branch ...)" to stderr too, so the message starts at the first line
    that says what went wrong, and leaves out hints. With no such line, it's all of stderr."""
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    first = next((i for i, line in enumerate(lines) if line.startswith(GIT_WRONG)), 0)
    said = [line for line in lines[first:] if not line.startswith("hint:")]
    return ParallaxError(f"git {' '.join(str(a) for a in args)} failed: {' '.join(said) or 'no error from git'}")


def _git(repo: Path, *args: str) -> str:
    # git speaks UTF-8; Windows' default (cp1252) crashes on diffs with characters like box drawing
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise git_failed(args, result.stderr)
    return result.stdout.strip()


SLUG_SKIP = {"a", "an", "the", "is", "are", "was", "were", "be", "for", "to", "of", "in", "on", "and", "or",
             "it", "its", "this", "that", "with", "should", "please", "we", "i", "my", "our"}


def _slug(text: str) -> str:
    """A few words of the goal for the branch name: 'the README install steps are wrong' -> readme-install-steps."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    kept = [w for w in words if w not in SLUG_SKIP][:3] or words[:3]
    return "-".join(kept)[:30].strip("-") or "task"


def worktrees_home(root: Path) -> Path:
    """Where a repo's task worktrees live: outside the repo, so none sits under a protected path.

    ~/.local/share/parallax/worktrees/<repo>-<hash of its path>, or under $XDG_DATA_HOME if set.
    """
    data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    root = Path(root).resolve()
    tag = hashlib.sha256(str(root).encode()).hexdigest()[:8]  # two repos with one name stay apart
    return Path(data) / "parallax" / "worktrees" / f"{root.name}-{tag}"


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
    def init(cls, root: Path, actor: str = "human", confirm_setup=None) -> "Project":
        """confirm_setup(command) -> bool: asked once when the repo's example policy carries a [build] setup
        command, since that runs as you. Without an answer of yes, the command is left out."""
        root = Path(root).resolve()
        refuse_inside_task(root)
        _git(root, "rev-parse", "--is-inside-work-tree")
        policy_path = root / POLICY_FILE
        if not policy_path.exists():  # the repo's own example if it has one, else the defaults
            example = root / EXAMPLE_POLICY_FILE
            text = example.read_text() if example.is_file() else DEFAULT_POLICY
            if confirm_setup is not None and example.is_file():
                setup = Policy.load(example).build["setup"].strip()
                if setup and not confirm_setup(setup):
                    text = re.sub(r"(?m)^(setup\s*=\s*).*$", r'\1""  # left out at init; it would run as you', text, count=1)
            policy_path.write_text(text)
        from .review import REVIEW_FILE, TEMPLATE as REVIEW_TEMPLATE
        if not (root / REVIEW_FILE).exists():
            (root / REVIEW_FILE).write_text(REVIEW_TEMPLATE, encoding="utf-8")
        (root / STATE_DIR).mkdir(exist_ok=True)
        (root / STATE_DIR / ".gitignore").write_text("*.lock\n")
        project = cls(root)
        if not project.ledger.entries():
            project.ledger.append("project.init", actor, "initialized",
                                  policy_sha=hashlib.sha256(policy_path.read_bytes()).hexdigest())
        return project

    @classmethod
    def find(cls, start: Path) -> "Project":
        for path in [Path(start).resolve(), *Path(start).resolve().parents]:
            if (path / POLICY_FILE).exists() and (path / STATE_DIR).exists():
                return cls(path)
        raise ParallaxError("not a parallax project here. run `parallax init` in your repo's folder")

    # tasks ---------------------------------------------------------------
    def new_task(self, goal: str, actor: str = "human", *, plan: bool = False, intent: bool = False) -> dict:
        """intent: the task follows the lifecycle (intent, plan, gates) instead of running from its goal."""
        refuse_inside_task(self.root)
        task_id = self._unused_task_id()
        branch = f"parallax/{task_id}-{_slug(goal)}"
        worktree = worktrees_home(self.root) / task_id
        base = _git(self.root, "rev-parse", "HEAD")
        _git(self.root, "worktree", "add", "-b", branch, str(worktree), base)
        self.ledger.append(
            "task.created", actor, goal,
            task=task_id, branch=branch, worktree=str(worktree), base=base, plan=plan, intent=intent,
        )
        return self.task(task_id)

    def _unused_task_id(self) -> str:
        """A task id this repo has never had: in no task.created entry, and no branch, worktree or task
        folder named for it. Ids are six hex digits so people can read them, so two can match: about a
        3% chance by a repo's thousandth task. A match draws another id; reusing one would put two tasks
        under one id in the ledger, or fail on the branch (CI run 36947648494)."""
        from .sandbox import task_home
        used = {e["data"].get("task") for e in self.ledger.entries() if e["kind"] == "task.created"}
        branches = _git(self.root, "for-each-ref", "--format=%(refname:short)", "refs/heads/parallax/")
        used |= {b.removeprefix("parallax/").split("-", 1)[0] for b in branches.splitlines()}
        for _ in range(100):
            task_id = uuid.uuid4().hex[:6]
            if task_id not in used and not (worktrees_home(self.root) / task_id).exists() \
                    and not task_home(self.root, task_id).exists():
                return task_id
        raise ParallaxError("no unused task id after 100 tries")

    def tasks(self) -> dict[str, dict]:
        return status.derive(self.ledger.entries())

    def task(self, task_id: str) -> dict:
        tasks = self.tasks()
        if task_id not in tasks:
            raise ParallaxError(f"no task {task_id}")
        return tasks[task_id]

    def git_at(self, task_id: str) -> Path:
        """Where to read a task's commits and trees: its worktree, or, once that's removed, the project
        root. They share one object store, so every tree and commit the ledger names is there too."""
        wt = Path(self.task(task_id)["worktree"])
        return wt if wt.is_dir() else self.root

    def diff(self, task_id: str, *args: str) -> str:
        """Working tree against the task's base. Content only, never commit messages. With the worktree
        removed, the accepted commit against the base, from the project root."""
        t = self.task(task_id)
        wt = Path(t["worktree"])
        if not wt.is_dir():
            acc = [e for e in self.ledger.entries() if e["kind"] == "task.accepted" and e["data"].get("task") == task_id]
            return _git(self.root, "diff", *args, t["base"], acc[-1]["data"]["commit"]) if acc else ""
        _git(wt, "add", "-N", ".")  # include new files in the diff without staging content
        return _git(wt, "diff", *args, t["base"])

    # inbox -----------------------------------------------------------------
    def inbox(self) -> list[dict]:
        """Disagreements and stops nobody has ruled on yet."""
        entries = self.ledger.entries()
        resolved = {e["data"]["decision"] for e in entries if e["kind"] == "decision.resolved"}
        return [e for e in entries if e["kind"] in INBOX_KINDS and e["id"] not in resolved]

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
