"""The programs a build needs, found before any task runs.

[build] setup and Accept and merge's test command run as you, with the PATH `parallax ui` was
started with. From a shell without uv on its PATH, setup failed with "uv not found" in the middle of
a task. So Parallax also looks where the installers put their programs: ~/.local/bin (uv, Claude
Code and Parallax itself all install there) and ~/.cargo/bin (where older uv installers went).
Those folders go after your PATH, never before, so they never shadow a program your PATH finds.
Only commands that already run as you get that PATH; Maker's stays the scrubbed one in build.py,
and the sandbox lets Maker write only its worktree, never these folders.

Whatever is still missing is named, with its fix, before any task runs: by `parallax doctor`, and by
`parallax ui` at startup and on its page. A build that hits one anyway stops with the same words.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass

INSTALL_DIRS = ("~/.local/bin", "~/.cargo/bin")
ALWAYS = (("git", "every task"), ("uv", "Parallax's own setup"), ("claude", "every agent"))
SANDBOX = ("srt", "node", "bwrap", "socat")  # sandbox.TOOLS: srt is a node script, bwrap and socat are what it runs
INSTALL = {"uv": "curl -LsSf https://astral.sh/uv/install.sh | sh",
           "claude": "curl -fsSL https://claude.ai/install.sh | bash",
           "git": "sudo apt install -y git"}
BUILTINS = {"export", "cd", "set", "unset", "source", ".", "true", "false", "echo", "printf", "test", "[", "fi",
            "done", "esac", "for", "case", "}", ")"}  # a step that starts with one of these runs no program by name
PREFIXES = {"exec", "env", "sudo", "time", "command", "if", "then", "else", "elif", "do", "while", "until", "!", "{", "("}
NOT_FOUND = (re.compile(r"command not found: ([\w.+-]+)"), re.compile(r"([\w.+-]+): (?:command )?not found"))  # zsh, then sh and bash
RESTART = 'add its folder to PATH in ~/.bashrc, for example export PATH="$HOME/.local/bin:$PATH", then restart parallax ui from a new terminal'


@dataclass(frozen=True)
class Missing:
    tool: str
    why: str  # what needs it

    @property
    def text(self) -> str:
        return f"{self.tool} not found: {self.why} needs it"

    @property
    def fix(self) -> str:
        if self.tool in SANDBOX:
            return f"install the sandbox tools as in README.md's Setup step 1. if {self.tool} is installed already, {RESTART}"
        how = f"install it with {INSTALL[self.tool]}" if self.tool in INSTALL else f"install {self.tool}"
        return f"{how}. if it's installed already, {RESTART}"

    @property
    def message(self) -> str:
        return f"{self.text}. fix: {self.fix}."


class MissingTool(Exception):
    """A build hit a program that isn't there. The card says which, and the fix."""

    def __init__(self, missing: Missing):
        super().__init__(missing.message)
        self.missing = missing


def path(environ: dict | None = None) -> str:
    """Your PATH, then the installers' folders that exist and aren't on it already."""
    current = (os.environ if environ is None else environ).get("PATH", "")
    have = current.split(os.pathsep) if current else []
    extra = [d for d in (os.path.expanduser(d) for d in INSTALL_DIRS) if os.path.isdir(d) and d not in have]
    return os.pathsep.join(have + extra)


def env(environ: dict | None = None) -> dict[str, str]:
    """The environment for a command that runs as you: yours, with the PATH above."""
    out = dict(os.environ if environ is None else environ)
    out["PATH"] = path(out)
    return out


def which(tool: str, environ: dict | None = None) -> str | None:
    return shutil.which(tool, path=path(environ))


def _steps(command: str) -> list[list[str]]:
    """The command's simple commands, as words, quotes respected: split at && || ; | & and newlines.
    A redirection's target is never a command."""
    lex = shlex.shlex(command.replace("\n", " ;\n"), posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    steps: list[list[str]] = [[]]
    skip = False
    for tok in lex:
        if skip:
            skip = False
        elif tok and set(tok) <= set("&|;()<>"):
            if "<" in tok or ">" in tok:
                skip = True  # the file it reads or writes comes next
            elif steps[-1]:
                steps.append([])
        else:
            steps[-1].append(tok)
    return [s for s in steps if s]


def commands(command: str) -> list[str]:
    """The programs a shell command runs by name: the first word of each step. Paths and shell
    builtins aren't looked up; a setup that runs ./script.sh can't be read further."""
    out: list[str] = []
    try:
        steps = _steps(command or "")
    except ValueError:  # unbalanced quotes: the shell would refuse it too, and say so when it runs
        return out
    for words in steps:
        while words and (re.match(r"^[A-Za-z_]\w*=", words[0]) or words[0] in PREFIXES):
            words = words[1:]
        if words and words[0] not in BUILTINS and "/" not in words[0] and words[0] not in out:
            out.append(words[0])
    return out


def needed(policy=None, platform: str | None = None) -> list[tuple[str, str]]:
    """(program, what needs it), each once: Parallax's own, the sandbox's, and the policy's commands."""
    platform = platform or sys.platform
    out = list(ALWAYS) + [(t, "the sandbox") for t in SANDBOX if platform == "linux" or t in ("srt", "node")]
    if policy is not None:
        out += [(t, "the [build] setup command") for t in commands(policy.build["setup"])]
        if policy.ui_tester["enabled"]:
            out += [(t, "the [ui_tester] start command") for t in commands(policy.ui_tester["start"])]
        out += [(t, "the [merge] test_command") for t in commands(policy.merge["test_command"])]
    first: dict[str, str] = {}
    for tool, why in out:  # the policy's reason is the more useful one: it names what you set
        first[tool] = why if tool not in first or why.startswith("the [") else first[tool]
    return list(first.items())


def missing(policy=None, environ: dict | None = None, find=None) -> list[Missing]:
    find = find or (lambda t: which(t, environ))
    return [Missing(t, why) for t, why in needed(policy) if not find(t)]


def for_command(command: str, why: str, environ: dict | None = None) -> Missing | None:
    """The first program a command runs that can't be found, before running it."""
    return next((Missing(t, why) for t in commands(command) if not which(t, environ)), None)


def not_found(output: str, why: str) -> Missing | None:
    """A shell's "<program>: not found" in a command's output, as the program it couldn't find."""
    for pattern in NOT_FOUND:
        m = pattern.search(output or "")
        if m:
            return Missing(m.group(1), why)
    return None
