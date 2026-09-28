Bottom line: Rewrite the WSL install steps in README.md so a reader following them in a fresh dedicated distro actually ends up with a working Parallax, because several steps fail as written.
Not looked at: whether Ubuntu 24.04's apt Node is new enough for `@anthropic-ai/sandbox-runtime` and Claude Code (no network here), and I did not run the steps on a fresh distro.

kind: docs
size: small
title: fixing the README install steps for WSL

## Problem
The Install section (README.md:36-104) reads as a straight path from a new Windows machine to `ready.`, but three steps break or mislead.

- Step 3 puts you in a root shell (`wsl -d parallax`, README.md:50). Step 4 then says "Then as your user" (README.md:56) without saying how to get there, and the default user only starts applying after the wsl.conf edit in step 6 (README.md:91-92). Nothing tells you to `su - <you>`.
- uv's installer (README.md:56) puts `uv` in `~/.local/bin`, which a shell that started before the install does not have on its PATH. Step 5's `uv tool install` (README.md:60) and step 7's `parallax doctor` (README.md:71) both fail with "command not found" in that same shell. The steps never mention opening a new shell or sourcing uv's env file.
- Step 5 clones from `/mnt/c` (README.md:59), so the clone's `origin` is a `/mnt/c` path. Step 6 then turns off automount (README.md:99), and that remote is unreachable from then on. `git fetch` and `git pull` break, and the steps do not say to repoint or drop the remote.
- The macOS and Linux paragraph (README.md:66) reads as if socat is needed on macOS. `check_sandbox` only asks for `srt` there, and for bubblewrap, socat and srt on Linux (parallax/doctor.py:87).

## Outcome
- Someone following steps 1 to 7 in a freshly imported Ubuntu-24.04 distro runs `parallax doctor` in step 7 and sees it resolve, with no step that needs a command the README did not give them.
- The steps say explicitly how to become your user, and where a new shell or `source ~/.local/bin/env` is needed before `uv` and `parallax` resolve.
- After step 6, the clone has no remote pointing into `/mnt/c`: the steps either repoint `origin` or say the remote is deliberately absent.
- The macOS and Linux paragraph lists exactly the tools `check_sandbox` requires per platform: `srt` on macOS, bubblewrap and socat and srt on Linux.
- The `parallax doctor` sample output in README.md:74-82 still matches what `report()` prints for a hardened WSL2 machine.
- `parallax lint docs/tasks/003876/intent.md` passes, and the test suite is unchanged and still green.

## Constraints
- Docs only. No change to `parallax/doctor.py`, its checks, its statuses, or any command name or flag.
- Keep the `### Harden WSL` heading text so the `README.md#harden-wsl` anchor in parallax/doctor.py:26 still resolves.
- Keep the stance from docs/direction.md:25: a dedicated WSL2 distro, interop and Windows PATH and drives off, repo in the WSL filesystem, UI opened from a Windows browser. Native Windows stays unsupported.
- Keep the `[claude]` extra name as declared in pyproject.toml:18.
- Do not add tools or services beyond what `check_sandbox` and `check_login` need.
- Keep the section order and the rest of the README as is, and keep the plain house style: short sentences, no em dashes.
