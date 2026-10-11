# macOS: what it would take

Parallax runs on Linux and WSL2. It's untested on macOS. This page is a feasibility check from 2026-10-11, made by reading the code and the [sandbox runtime's README](https://github.com/anthropics/sandbox-runtime), with no Mac to try it on. The result: not done yet, because the part that matters most, proving the sandbox holds, can only be checked on a real Mac or a macOS CI runner, and the change is bigger than it looks.

## What already works on macOS, on paper

- **The sandbox runtime supports it.** `srt` uses `sandbox-exec` with a generated Seatbelt profile on macOS, and needs no bubblewrap, socat or ripgrep there. Claude Code's own sandbox, which runs Maker's Bash, does the same.
- **Parallax already expects it in places.** `parallax doctor` reports `platform macos ok` and asks only for `srt` (`check_platform`, `check_sandbox` in `parallax/doctor.py`). `tools.needed` asks for bwrap and socat on Linux only. `build.is_alive` falls back to `kill(0)` where there's no `/proc`. The memory cap runs the command uncapped where there's no `/proc`.
- **The rules are the same file.** `sandbox.Rules.srt()` and `claude_settings()` write one set of allow and deny lists that both platforms read.

## What would have to change

1. **Prove the sandbox on macOS.** Preflight's real-sandbox tests (`tests/sandboxcheck.py`, `test_preflight_against_the_real_sandbox`) must pass there: no writes to the shared `.git` or a protected path, no reads of the home folder or the approval key, no network outside the plan. Seatbelt matches real paths, and on macOS `/tmp` and `/var` are symlinks into `/private`, so every rule path needs resolving first, and the tests' temp folders sit under `/private/var/folders`. Today `tests/sandboxcheck.py` asks for bwrap and socat before it starts the sandbox, so on macOS those tests would skip and a green run would prove nothing; it has to ask only for `srt` there. The runtime's README notes that `allowLocalBinding` on macOS lets a command reach loopback ports without the proxy; Parallax sets it to false, which has to be confirmed by a probe.
2. **The memory cap.** `parallax/memcap.py` reads `/proc`. On macOS every capped run (the suite, Reticle's and Field's runs, the eval runs) would have no cap. It needs a second way to measure the process tree (for example `ps -o rss`), or the docs must say macOS runs uncapped.
3. **Field, the UI tester.** `uitest.ensure_tools` fetches Chromium's libraries with `dpkg` into `lib/usr/lib/x86_64-linux-gnu`. On macOS Chromium needs none of that, but the sandbox rules for the browser and the pinned `@playwright/mcp` (pinned because later versions need Unix sockets bwrap refuses) would need checking again under Seatbelt, where Unix sockets are allowed by path.
4. **What a stopped sandbox says.** `parallax/sandboxfail.py` matches only what bwrap and srt print on Linux, and the card's hint names the AppArmor `sysctl` and WSL. A `sandbox-exec` failure would fall through to the general message.
5. **Setup and docs.** The README's setup is apt and NodeSource. macOS would be Homebrew (git, node, uv) and `npm install -g @anthropic-ai/sandbox-runtime`, with no sysctl.
6. **Tests that assume Linux.** At least `tests/test_memcap.py` (skips without `/proc` already), `tests/test_ui_tester.py` and `tests/test_ui_browser.py` (the Linux library fetch, Chromium's system libraries), `tests/test_sandbox_and_preflight.py` (bwrap's messages and namespace checks), `tests/test_sandboxfail.py` and `tests/test_decide_and_redraft.py` (the Linux hint text), and `tests/test_doctor.py` (fakes a Linux machine, so it should pass). Each would need a marker that says why it skips on macOS, never a silent skip of a sandbox check.

## A plan

1. Add a `macos-latest` job to CI that installs uv, Node 22 and `srt`, and runs `scripts/test.sh` with the Linux-only tests marked. Expect the first runs to fail; read them before changing code.
2. Make the sandbox rules resolve symlinked paths, and get the real-sandbox preflight tests passing on that job. This is the gate: without it, macOS isn't supported, whatever else works.
3. Give the memory cap a macOS reader, or say plainly that macOS runs uncapped.
4. Leave Field off on macOS until its sandbox is checked there; `parallax doctor` says so.
5. Add the macOS sandbox failure lines to `sandboxfail.py`, and a macOS setup section to the README.

Steps 1 and 2 are the size of the risk: the rest is a few hundred lines, but nothing can be called supported until a macOS runner shows the sandbox refusing what it should.
