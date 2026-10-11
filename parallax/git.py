"""Running git: the one helper every module uses, so each call reads its output the same way.

run() never raises on git's exit code; out() returns git's stdout and raises git_failed when git
fails. Output is UTF-8 with bad bytes replaced (git speaks UTF-8; Windows' default, cp1252, crashed
on diffs with characters like box drawing), or bytes with text=False. env adds variables to this
process's environment, such as GIT_INDEX_FILE for a throwaway index.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_WRONG = ("fatal:", "error:")


def git_failed(args, stderr: str | bytes):
    """A failed git command, said with the command and git's own error. git writes progress lines such
    as "Preparing worktree (new branch ...)" to stderr too, so the message starts at the first line
    that says what went wrong, and leaves out hints. With no such line, it's all of stderr."""
    from .core import ParallaxError  # core imports this module, so not at the top
    if isinstance(stderr, bytes):
        stderr = stderr.decode(errors="replace")
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    first = next((i for i, line in enumerate(lines) if line.startswith(GIT_WRONG)), 0)
    said = [line for line in lines[first:] if not line.startswith("hint:")]
    return ParallaxError(f"git {' '.join(str(a) for a in args)} failed: {' '.join(said) or 'no error from git'}")


def run(where: Path, *args: str, env: dict | None = None, input: str | bytes | None = None,
        text: bool = True) -> subprocess.CompletedProcess:
    """git -C where ARGS, with its output captured, whatever its exit code."""
    return subprocess.run(["git", "-C", str(where), *args], capture_output=True, input=input,
                          env={**os.environ, **env} if env else None, text=text,
                          encoding="utf-8" if text else None, errors="replace" if text else None)


def out(where: Path, *args: str, strip: bool = True, **kw) -> str | bytes:
    """git's stdout, stripped unless strip=False. A failure raises git_failed with git's error (its
    stdout when stderr is empty)."""
    done = run(where, *args, **kw)
    if done.returncode != 0:
        raise git_failed(args, done.stderr or done.stdout)
    return done.stdout.strip() if strip else done.stdout
