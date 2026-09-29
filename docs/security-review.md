# Security review, 2026-09-29

A cold sweep of the repo before it went public, against [THREAT_MODEL.md](THREAT_MODEL.md). Every fix below has a test. Nothing here changed what the threat model says is out of reach: anything running as your OS user can still do anything.

## Findings and fixes

| Severity | Finding | Fixed how |
|---|---|---|
| High | The launch rule approved a plan that opened the boundary. A drafter steered by repo or issue text could list `outside_reads` or `domains`, and a cap under `auto_launch_usd` launched it unseen. | A plan with any `domains` or `outside_reads` waits for you; the reason names the field. (`pilot.launch_rule`) |
| Medium | A plan's `outside_reads` could name the approval key's folder or `~/.claude`; preflight probed two exact files, not the folders. | No plan can open either folder; the build refuses before rules are written. (`sandbox.refused_reads`) |
| Medium | CI ran with default token permissions and actions pinned to tags. | `permissions: contents: read`; every action pinned to a commit; a test keeps it so. |
| Medium | The whole sandbox rested on preflight running in each launcher; the background module by hand skipped it. | Preflight runs in `build.run_build` and nowhere else; one test drives every entry point. |
| Low | A task id in a UI route became a path one level up. | The id must be a known task. |
| Low | A bad `Content-Length` crashed the request thread; 500s echoed exception text. | 400 or 413; 500s return one fixed line and log the detail to the terminal. |
| Low | The secrets scan missed common key shapes. | Eight more patterns (OpenAI project keys, Stripe, GitLab, npm, PyPI, Hugging Face, Slack webhooks, JWTs). Still best effort. |
| Low | `parallax init` copied a repo's example policy, `[build] setup` included, which runs as you. | `init` shows the command and asks once; non-interactive means left out. |
| Low | Two threat-model claims had no code behind them: no memory across tasks, and `file:` blocked in Field's browser. | Auto-memory is off for every agent in its environment and the maker's settings, per the Claude Code docs; the `file:` claim names the pinned MCP version it relies on, and the pin test says to recheck it if the version moves. |

## Knowingly open

- **The `[build] setup` command runs as you**, on a fresh copy of the base commit. That is by design, the same trust as running a repo's Makefile; `init` now asks before adopting one.
- **Second Eye reads the diff and can't run code.** A test Maker wrote that passes without the change, or that exercises nothing, is caught only if Second Eye notices. Proving tests bite (a verifier that writes tests from the intent before the build) is planned, not built.
- **The hash chain proves only that the sandboxed agents didn't tamper.** Approvals are HMAC signatures with your key; a third party can verify the chain and the file hashes, not who approved. An asymmetric signature would close that.
- **The stale-run check depends on someone running a command or having the UI open.** A machine asleep with a task mid-run is noticed when it wakes.
- **The secrets scan is pattern matching.** Split, encoded or unknown formats get through; the sandbox, not the scan, is the rail.

Report anything new through GitHub's private vulnerability reporting, as [SECURITY.md](../SECURITY.md) says.
