"""The build's OS sandbox, generated from the approved plan.

One function makes the Bash-layer rules. They go into two files, both written outside the
worktree so the maker can't change its own configuration:
- settings.json, for the maker's Claude Code (loaded with --settings; setting_sources=[] means
  the worktree's own .claude/ is ignored);
- srt.json, the same rules for the sandbox runtime, which preflight uses to test them.

Rules:
- writes: the worktree only, minus every protected path (they're denied even if they don't
  exist yet), and minus the shared .git directory, which Claude Code would otherwise open for
  linked worktrees;
- reads: $HOME and /mnt are denied, except the worktree, the task's venv, the shared .git
  directory (read-only, so git works) and the plan's outside reads;
- network: denied, except the plan's domains. No prompts: strictAllowlist;
- no escape hatch: allowUnsandboxedCommands is false, and failIfUnavailable refuses to run
  without a sandbox.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .approvals import key_path
from .core import ParallaxError
from .guard import PROTECTED_ANY_DEPTH, PROTECTED_FROM_ROOT

ROOT_TARGETS = (".parallax", "parallax.policy.toml", "mission.md", "CLAUDE.md", ".claude", ".mcp.json",
                "REVIEW.md", "docs/parallax.md", "docs/tasks", ".git")


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "parallax"


def task_home(root: Path, task_id: str) -> Path:
    """Parallax's own files for a task (settings, logs, venv): outside the worktree and the repo."""
    root = Path(root).resolve()
    tag = hashlib.sha256(str(root).encode()).hexdigest()[:8]
    return data_home() / "tasks" / f"{root.name}-{tag}" / task_id


def shared_git_dir(worktree: Path) -> Path:
    out = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode != 0:
        raise ParallaxError(f"can't find the shared .git directory: {out.stderr.strip()}")
    return Path(out.stdout.strip()).resolve()


def protected_targets(worktree: Path) -> list[Path]:
    """Every path in the worktree the maker may never write, plus nested copies that exist now.

    Nested ones created later can't be denied by bubblewrap in advance; the tool layer and the
    check of the final diff catch those.
    """
    wt = Path(worktree)
    out = [wt / t for t in ROOT_TARGETS]
    names = {n.casefold() for n in PROTECTED_ANY_DEPTH}
    for dirpath, dirnames, filenames in os.walk(wt):
        here = Path(dirpath)
        for name in [*dirnames, *filenames]:
            if name.casefold() in names and here != wt:
                out.append(here / name)
        dirnames[:] = [d for d in dirnames if d.casefold() not in names]  # denied as a whole already
    for prefix in PROTECTED_FROM_ROOT:
        p = wt.joinpath(*prefix)
        if p not in out:
            out.append(p)
    return list(dict.fromkeys(out))


# Inside a directory that is write-denied as a whole, bubblewrap can't create the mount points the
# sandbox runtime adds for its own always-denied paths, and the sandbox fails to start. Creating
# them first, empty, avoids that. git doesn't track empty directories, so the diff never shows them.
RUNTIME_MOUNTS = {".claude": ("commands", "agents"), ".git": ("hooks",)}


def prepare_mount_points(targets: list[Path]) -> None:
    for t in targets:
        if t.is_dir() and t.name.casefold() in RUNTIME_MOUNTS:
            for child in RUNTIME_MOUNTS[t.name.casefold()]:
                (t / child).mkdir(exist_ok=True)


# Claude Code 2.1's sandbox leaves empty placeholder files at these names in the working directory
# (seen live in M9). Parallax removes them after a build, but only when new, empty and untracked.
SANDBOX_LEFTOVERS = {
    ".env", ".env.local", ".env.development", ".env.development.local", ".env.production",
    ".env.production.local", ".env.test", ".env.test.local", ".gitmodules", ".npmrc", ".yarnrc",
    ".yarnrc.yml", "bunfig.toml", "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
}


def untracked(worktree: Path) -> set[str]:
    out = subprocess.run(["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", "-z"],
                         capture_output=True, text=True)
    return {p for p in out.stdout.split("\0") if p}


def remove_leftovers(worktree: Path, before: set[str]) -> list[str]:
    """Remove the sandbox's empty placeholder files. Returns what was removed."""
    removed = []
    for rel in sorted(untracked(worktree) - before):
        path = Path(worktree) / rel
        if Path(rel).name in SANDBOX_LEFTOVERS and path.is_file() and not path.is_symlink() \
                and path.stat().st_size == 0:
            path.unlink()
            removed.append(rel)
    return removed


@dataclass
class Rules:
    """The Bash layer: what sandboxed commands may read, write and reach."""
    allow_write: list[str]
    deny_write: list[str]
    deny_read: list[str]
    allow_read: list[str]
    domains: list[str]

    def srt(self) -> dict:
        return {
            "network": {"allowedDomains": self.domains, "deniedDomains": [], "allowLocalBinding": False},
            "filesystem": {"allowWrite": self.allow_write, "denyWrite": self.deny_write,
                           "denyRead": self.deny_read, "allowRead": self.allow_read},
        }

    def claude_settings(self) -> dict:
        return {
            "sandbox": {
                "enabled": True,
                "failIfUnavailable": True,
                "allowUnsandboxedCommands": False,
                "autoAllowBashIfSandboxed": False,  # every Bash call still passes Parallax's hook
                "excludedCommands": [],
                "filesystem": {"allowWrite": self.allow_write, "denyWrite": self.deny_write,
                               "denyRead": self.deny_read, "allowRead": self.allow_read},
                "network": {"allowedDomains": self.domains, "strictAllowlist": True, "allowLocalBinding": False},
            },
            # the tool layer is Parallax's hook; these deny rules are a second copy of it ("//" = absolute)
            "permissions": {"deny": [f"Edit(/{p}{tail})" for p in self.deny_write for tail in ("", "/**")]},
        }


def rules(worktree: Path, targets: list[Path], *, git_dir: Path | None, venv: Path | None,
          reads: list[str], domains: list[str]) -> Rules:
    wt = str(Path(worktree))
    deny_write = [str(t) for t in targets] + ([str(git_dir)] if git_dir else [])
    allow_read = [wt] + ([str(git_dir)] if git_dir else []) + ([str(venv)] if venv else []) + list(reads)
    # the approval key's folder is denied by name too, wherever XDG_CONFIG_HOME puts it
    deny_read = list(dict.fromkeys([str(Path.home()), "/mnt", str(key_path().parent)]))
    return Rules(allow_write=[wt], deny_write=deny_write, deny_read=deny_read,
                 allow_read=list(dict.fromkeys(allow_read)), domains=list(domains))


def write_configs(home: Path, r: Rules) -> tuple[Path, Path]:
    """settings.json for Claude Code and srt.json for the runtime, both outside the worktree."""
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    settings, srt = home / "settings.json", home / "srt.json"
    settings.write_text(json.dumps(r.claude_settings(), indent=2))
    srt.write_text(json.dumps(r.srt(), indent=2))
    return settings, srt
