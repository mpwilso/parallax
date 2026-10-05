"""A sandbox that didn't start: the line of bwrap's or srt's output that says why, and the card's hint.

One place for it, used by preflight (which keeps what srt printed when its probe never ran) and by
the card for any stop. It matches only what bwrap and srt really print. Each pattern names the file
under tests/fixtures/real/ that holds that output verbatim, or says it is unverified.
"""
from __future__ import annotations

from dataclasses import dataclass

NAMESPACE = "namespace"  # the user namespaces the sandbox needs: the README's sysctl line, and docs/wsl.md on WSL
TOOLS = "tools"  # a program the sandbox needs isn't there

# (text in the line, what it means, the real output it was seen in, or None: unverified)
PATTERNS = (
    # new user namespaces forbidden (ENOSPC). bwrap's other "Creating new namespace failed, likely because the
    # kernel does not support user namespaces" starts the same way; that one is in bwrap's strings, never seen printed
    ("bwrap: Creating new namespace failed", NAMESPACE, "bwrap-0.9.0-disable-userns.stderr"),
    # the kernel refuses a new user namespace (EPERM)
    ("bwrap: No permissions to create new namespace", NAMESPACE, "bwrap-0.9.0-chroot-eperm.stderr"),
    # unverified: in bwrap 0.9.0's own strings, never seen printed here
    ("bwrap: setting up uid map", NAMESPACE, None),
    # srt can't find bwrap, socat or rg
    ("Error: Sandbox dependencies not available:", TOOLS, "srt-1.0.0-no-tools-on-path.stderr"),
)


@dataclass(frozen=True)
class Failure:
    line: str  # from the matched text to the end of its line, as printed
    kind: str


def read(text: str) -> Failure | None:
    """The first line that says why the sandbox didn't start, or None. Another tool's error that only
    mentions a namespace, such as kubectl's, matches nothing."""
    for line in (text or "").splitlines():
        for pattern, kind, _ in PATTERNS:
            at = line.find(pattern)
            if at != -1:
                return Failure(line[at:].strip(), kind)
    return None


def hint(failure: Failure | None, wsl: bool) -> list[str]:
    """One line: the next action, naming parallax doctor without running it."""
    if failure is None:
        return []
    if failure.kind == NAMESPACE:
        where = ('see "Allow user namespaces" in docs/wsl.md and the sysctl line in the README\'s setup step 1' if wsl
                 else "use the sysctl line in the README's setup step 1")
        return [f"To fix it, allow user namespaces ({where}), then retry; parallax doctor checks the sandbox once it's set."]
    return ["To fix it, install what it names, then retry; parallax doctor lists the sandbox tools it finds."]


def on_wsl() -> bool:
    from .doctor import Machine, is_wsl
    return is_wsl(Machine(needed=[]))  # is_wsl reads only the platform and the kernel's release
