"""A memory cap on a process and everything it starts, so a runaway fails its own run, not the machine.

Seen on 2026-09-30: a test Reticle wrote looped forever on the base of boltons-319 (daterange never
advanced), filled a list at about 114 MB a second, and took all of WSL down at 31.7 GB.

The cap watches the process tree, not one process: every few tenths of a second it adds up the
memory each process really holds (its proportional share, swap included, so pages a browser
shares aren't counted twice) and kills the whole tree once the total passes the limit. It needs
nothing but /proc, so it works the same in CI and locally, with no systemd and no root. It can't
use an address-space limit (ulimit -v): node and Chromium reserve far more than they use and would
fail at start. Where there's no /proc the command runs uncapped.

A process that detaches and leaves the tree (a new session whose parent exits) isn't counted.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

GB = 1024 ** 3
SUITE = 4 * GB     # scripts/test.sh: this repo's own tests
EVAL = 4 * GB      # parallax eval: the whole run
TEST_RUN = 3 * GB  # one run of a repo's tests in the sandbox, under EVAL so it trips first and is recorded
POLL = 0.2
EXIT = 137  # what a shell shows for a killed process
ENV = "PARALLAX_MEMORY_CAP"  # the limit in bytes, set for a capped command
ENV_PID = "PARALLAX_MEMORY_CAP_PID"  # the process that watches it
PROC = Path("/proc")


def size(text: str) -> int:
    """4G, 512M or a plain number of bytes."""
    units = {"G": GB, "M": 1024 ** 2, "K": 1024}
    t = text.strip().upper().removesuffix("B")
    return int(float(t[:-1]) * units[t[-1]]) if t[-1:] in units else int(t)


def shown(n: int) -> str:
    return f"{n / GB:.2f} GB"


def message(what: str, used: int, limit: int) -> str:
    return f"memory cap: {what} used {shown(used)}, over the {shown(limit)} limit, so it was stopped"


def available() -> bool:
    return (PROC / "self" / "stat").exists()


def tree(root: int) -> list[int]:
    """root and every process under it."""
    children: dict[int, list[int]] = {}
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        ppid = int(stat.rsplit(")", 1)[1].split()[1])
        children.setdefault(ppid, []).append(int(entry.name))
    out, todo = [], [root]
    while todo:
        pid = todo.pop()
        out.append(pid)
        todo.extend(children.get(pid, []))
    return out


def held(pid: int) -> int:
    """Bytes a process holds: its share of resident pages plus its share of swap."""
    try:
        text = (PROC / str(pid) / "smaps_rollup").read_text()
        return sum(int(line.split()[1]) * 1024 for line in text.splitlines()
                   if line.startswith(("Pss:", "SwapPss:")))
    except (OSError, ValueError, IndexError):
        pass
    try:  # no smaps_rollup (an older kernel, or not ours to read): resident pages, shared ones too
        return int((PROC / str(pid) / "statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def used(root: int) -> int:
    return sum(held(p) for p in tree(root))


def kill_tree(root: int, include_root: bool = True) -> None:
    for pid in reversed(tree(root)):  # the youngest first, so nothing is left to start more
        if pid == root and not include_root:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


@dataclass
class Result:
    code: int
    output: str = ""
    over: bool = False
    peak: int = 0
    timed_out: bool = False


def run(argv: list[str], limit: int, what: str = "the command", timeout: float | None = None,
        capture: bool = False, **popen) -> Result:
    """Run argv under the cap. With capture, stdout and stderr come back together as text.
    Over the limit, the whole tree is killed and the result says so: code EXIT, over True, and the
    output ends with the cap's message. Past timeout, the tree is killed and the code is 124."""
    env = {**(popen.pop("env", None) or os.environ), ENV: str(limit), ENV_PID: str(os.getpid())}
    if capture:
        popen.update(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    proc = subprocess.Popen(argv, env=env, **popen)
    watching = available()
    start, peak, chunks = time.monotonic(), 0, []
    while True:
        try:
            out, _ = proc.communicate(timeout=POLL)
            chunks.append(out or "")
            return Result(proc.returncode, "".join(chunks), False, peak)
        except subprocess.TimeoutExpired:
            pass
        if watching:
            peak = max(peak, used(proc.pid))
        if peak > limit or (timeout is not None and time.monotonic() - start > timeout):
            kill_tree(proc.pid)
            out, _ = proc.communicate()
            chunks.append(out or "")
            if peak > limit:
                note = message(what, peak, limit)
                if not capture:
                    print(note, file=sys.stderr, flush=True)
                return Result(EXIT, "".join(chunks) + "\n" + note, True, peak)
            return Result(124, "".join(chunks), False, peak, timed_out=True)


def guard(limit: int, what: str = "this run") -> threading.Thread | None:
    """Cap this process and everything it starts, from a watcher thread. Over the limit, it prints
    the cap's message, kills every process under this one, then this one. None without /proc."""
    if not available():
        return None
    me = os.getpid()
    os.environ[ENV], os.environ[ENV_PID] = str(limit), str(me)

    def watch():
        while True:
            now = used(me)
            if now > limit:
                print(message(what, now, limit), file=sys.stderr, flush=True)
                kill_tree(me, include_root=False)
                os._exit(EXIT)
            time.sleep(POLL)

    t = threading.Thread(target=watch, name="memory-cap", daemon=True)
    t.start()
    return t


def main(argv: list[str] | None = None) -> int:
    """python -m parallax.memcap 4G -- command args..."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 3 or args[1] != "--":
        print("usage: python -m parallax.memcap LIMIT -- command [args...]", file=sys.stderr)
        return 2
    limit, cmd = size(args[0]), args[2:]
    try:
        return run(cmd, limit, what=os.path.basename(cmd[0])).code
    except KeyboardInterrupt:  # the command got the same Ctrl+C
        return 130


if __name__ == "__main__":
    sys.exit(main())
