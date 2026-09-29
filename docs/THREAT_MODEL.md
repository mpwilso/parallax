# Threat model

**Bottom line:** Parallax keeps the maker's work from running as you, or reaching your branch, without review. It can't protect anything from code that already runs as you.
**Not looked at:** the Claude Code and sandbox-runtime code themselves, and any other process you run.

## What we protect, and from whom

The adversary is the maker: a model working in a worktree, which may be wrong, confused, or steered by text it reads (issue text, repo content, web pages). The goal is narrow: **nothing the maker writes runs as you, or gets merged, without review.**

Out of reach, stated plainly: Parallax runs as your OS user. Anything else running as you (your shell, an editor extension, a compromised package you installed) can read the approval key, rewrite the ledger, and edit any file. No design inside one user account fixes that. A dedicated WSL2 distro narrows what that user can reach; it doesn't change the rule.

## The two layers

1. **The OS sandbox** (Claude Code's Bash sandbox, bubblewrap on Linux and WSL2). Every shell command the maker runs: writes only in its worktree and never to the protected paths or the shared `.git`; no reads of `$HOME`, `/mnt` or the approval key except the worktree, the task's venv, the shared `.git` (read-only) and the plan's reads; no network except the plan's domains; no unsandboxed escape hatch; and it refuses to run without a sandbox. Parallax generates these rules from the approved plan and writes them outside the worktree.
2. **The tool layer** (Parallax's hook on every tool call). Read, Edit, Write, NotebookEdit, WebFetch and anything else. Protected paths are refused whatever any setting says; anything the plan doesn't list is refused; "ask" is refused and recorded.

Preflight tests both before every launch, with the build's own rules, and refuses to launch if either would let a protected path be written. Preflight tests the rules in `srt` 1.0; the maker's commands run in Claude Code's own copy of the sandbox runtime. They were checked against each other live (M9), not on every launch.

## Enforced, best effort, out of reach

| Threat | What stops it | Strength |
|---|---|---|
| **Ledger forgery** | Append-only, hash-chained; `parallax verify` catches an edit. The maker can't write `.parallax/`. `Ledger-Head` in each accept commit anchors the chain in git. | The chain proves only that the sandboxed maker didn't tamper. Anything running as you can rewrite the whole chain consistently. |
| **Self-approval** | A gate counts only with an HMAC signature from `~/.config/parallax/key`, over the task, the gate and every file's hash. The sandbox can't read the key; a task's process can't run approve. Under the policy's launch rule, the pilot (running as you, outside the sandbox) signs the approval and names the rule in the ledger. | Enforced against the maker. Anyone who can read the key can approve, and so can anyone who can edit the policy file's launch rule. |
| **Someone else using the UI** | The page listens on 127.0.0.1 only, and every API call needs the link's token; Host and Origin are checked; a strict CSP forbids inline and outside script; agent text is only ever set as text. The token is kept, with the project's port, in a 0600 file beside the approval key, which the sandbox can't read, so a bookmark survives restarts. `parallax ui --new-token` replaces it. The sandbox also has no route to the port. | Enforced against the maker. Anyone who can read your config folder, or your browser's bookmarks and history, can use the page until you rotate the token. |
| **The UI tester seeing more than it should** | It gets the intent's outcomes and the app's URL, nothing else, and no shell; its file tools stop at its own folder. Its browser can reach only the app's origin (`--allowed-origins`), `file:` is blocked, and the sandbox it shares with the app has no network beyond loopback and can't read your home. | Enforced for what it can reach. The app runs from the built tree in that same sandbox, so a tester that gets the app to serve its own files could read them. |
| **Changing the UI tester's tests** | They live in `docs/tasks/<task>/ui_flows/`, which only Parallax writes and no agent can reach in either layer. Each is hashed when kept; a changed hash stops the check and comes to you. | Enforced. |
| **The pinned Playwright MCP server** | The UI tester's browser server is pinned at `@playwright/mcp` 0.0.70 (with `@playwright/test` 1.60.0-alpha-1774999321000, the same core). Every later version tried, 0.0.72 to 0.0.83, puts its browser behind a Unix socket, and the sandbox refuses Unix sockets on Linux. | Known limit. Upgrading breaks the tester: every browser call fails with `listen EPERM` on the socket, so each UI task comes to you as "the UI tester never reached the app". Allowing Unix sockets in the sandbox would fix that but open every socket on the host (a Docker or SSH agent socket, say), so don't. The pin also means no newer MCP fixes until a version works without the socket; `uitest.MCP_VERSION` is the one place to change, and the Part 3 spike in docs/plan.md is how to test one. |
| **Changing approved files** | Approvals record each file's hash; a changed file blocks the next gate, the build and accept. `docs/tasks/` is protected in both layers. | Enforced. |
| **Leaking to the checker** | The checker gets exactly outcome, constraints, REVIEW.md and the diff; one test pins it, on re-review too. It has no tools and no files. | Enforced by code. The diff itself can carry text aimed at the checker; see prompt injection. |
| **Tampering with test harnesses** | Parallax runs the plan's tests itself, in the sandbox, with conftest, pytest settings and package scripts taken from the base branch. The maker's report doesn't count. | Enforced for the listed harness files. A test the maker writes can still be weak; the checker reviews it. |
| **Planted hooks and auto-run files** | `.git` is protected, and accept commits with `commit-tree`, which runs no hooks. Files that run automatically (CI, Makefile, package scripts, `.envrc`, conftest) are flagged at Ready as a boundary change. | Flagged, not blocked: once merged, they run as you when their tool runs. Read them. |
| **Secrets exfiltration** | The builder starts from a scrubbed environment; the sandbox hides `$HOME` and has no network outside the plan. The secrets scan at accept looks for keys in the change. | The sandbox is the rail. The scan is best effort: split, encoded or unknown formats get through. A plan that allows a domain allows sending data to it. |
| **Prompt injection through repo content or issue text** | Outside text is data: it fills the task description and can't choose actions, targets or policy. Everything the maker can do is bounded by the two layers; drafters and the checker have no shell. | Best effort for the model's judgment; enforced for what it can reach. An injected maker can still write a bad change inside its worktree. That's what the check and your review are for. |
| **Command-pattern matching** | The tool layer refuses shell commands that name a protected path. | Best effort by design: `sh -c`, `python -c` and friends get around it. The real rails for merge and deploy are that the sandbox has no credentials and no route. |
| **The budget cap being bypassed** | Every call gets what's left of the cap as its own ceiling; Parallax sums the ledger before and after every call and stops the task at the cap. | One call can overshoot by the turn in flight. The figures are estimates at API list prices; on a subscription your real limit is the plan's usage limits, which Parallax can't see. |

## What a signed accept commit proves

If you set a signing key, accept signs its commit with it. The maker has no key and no route to one, so a signed accept commit is one that `parallax accept`, running as you, produced. It proves who made the commit, not that the change is right: the review, the tests and your merge do that. It doesn't prove the ledger wasn't rewritten before accept (anything running as you could have), and an unsigned commit proves nothing either way.

Trailers (`Parallax-Task`, `Approved-By`, `Verified-By`, `Ledger-Head`) are a record, not a control. Anyone can write them into a commit message.

## OWASP Top 10 for Agentic Applications (2026)

| Category | What Parallax does |
|---|---|
| ASI01 Agent Goal Hijack | The goal is your approved intent and plan, hashed and signed; outside text is data. The blind checker judges the diff against the intent, not the maker's story. |
| ASI02 Tool Misuse | Every tool call passes the tool layer; shell commands also run in the OS sandbox; nothing outside the plan crosses the boundary. |
| ASI03 Identity and Privilege Abuse | The maker holds no credentials: scrubbed environment, `$HOME` hidden, no key. Only you can approve, with a key the sandbox can't read. |
| ASI04 Agentic Supply Chain Vulnerabilities | New dependencies must be in the approved plan, and changed dependency manifests are checked by code. Packages themselves: out of scope for a local tool. |
| ASI05 Unexpected Code Execution | The maker's code runs only in the sandbox; setup runs as you only on a fresh copy of the base commit; accept runs no hooks; auto-run files are flagged at Ready. |
| ASI06 Memory and Context Poisoning | No agent keeps memory across tasks. The ledger is the only state, append-only and hash-chained. |
| ASI07 Insecure Inter-Agent Communication | Agents never talk to each other. Parallax passes files and diffs between them, and decides what each sees. |
| ASI08 Cascading Failures | One task, one worktree, one budget cap; a failure stops that task and comes to you. Stuck detection and the rework cap stop loops. |
| ASI09 Human-Agent Trust Exploitation | Outputs are short and in one shape, with every fact cited or labeled Unverified; decisions you can't read in 30 seconds are a lint failure. Merge is yours alone. |
| ASI10 Rogue Agents | The maker runs only inside a task, in the sandbox, under a cap, and `parallax stop` ends every build. It can't create tasks or decide anything. |
