<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/lockup-animated-dark.svg">
    <img src="docs/brand/lockup-animated-light.svg" alt="Parallax" height="72">
  </picture>
</p>

<p align="center"><a href="https://github.com/mpwilso/parallax/actions/workflows/tests.yml"><img src="https://github.com/mpwilso/parallax/actions/workflows/tests.yml/badge.svg" alt="CI"></a></p>

**Agents do the work. You make the calls.**

For developers who use AI coding agents. The agents build in a sandbox: a walled-off copy of your repo, where they can't reach your keys, your other files or the network beyond what the plan you approved lists. You make one decision per task: accept or send back.

Status: a portfolio project, built to show how I design, test and judge an AI tool. In the eval of 2026-10-01, Parallax took on 11 real bugs from open-source projects, and all 11 reached Ready (every check passed, and the task waited on one decision from a person). 10 of the fixes passed the tests the projects' maintainers wrote for them, which no agent saw, so one Ready was wrong: the fix did what the issue asked, but not everything the maintainers' tests checked. Focus, Second Eye and Reticle ran on `claude-sonnet-5-5`, Maker on `claude-opus-5` ([docs/evals.md](docs/evals.md#results)).

<p align="center"><img src="docs/brand/demo.gif" alt="The app: a task typed in, Focus, Reticle, Maker and Second Eye taking their turns, the card turning Ready, and the accept" width="800"></p>

<p align="center"><sub>Demo with scripted agents, so it runs in seconds and costs nothing. Real runs are in <a href="docs/ui-runs/">docs/ui-runs/</a>.</sub></p>

**3 min 25 s from request to Ready, $0.92 in agent costs (estimate), 1 decision: accept.** One real task, a one-line README fix, run through the UI on 2026-09-28, before Reticle joined the pipeline. All three real runs, with every screen and cost: [docs/ui-runs/](docs/ui-runs/README.md).

## What it does

<p align="center"><img src="docs/brand/flow.svg" width="900" alt="How a task moves: you describe it, Focus writes a plan, Parallax checks the plan and starts the build when it's small and safe, Reticle writes tests of what you asked, Maker builds in a sandbox, your tests, Reticle's, Second Eye and Field check it, and one card comes back for your accept and your merge, every step recorded in the tamper-evident ledger"></p>

<picture><source media="(prefers-color-scheme: dark)" srcset="docs/brand/party-animated-dark.svg"><img src="docs/brand/party-animated-light.svg" alt="Focus, Reticle, Maker, Second Eye and Field" width="450"></picture>

**Focus** drafts the intent and plan. **Reticle** writes tests of what you asked, before the build; Maker can't see or change them. **Maker** builds in the sandbox. **Second Eye** is the blind checker: it gets the goal, the review rules and the change itself (the diff: the lines added and removed), never the plan or the builder's notes, and a test pins that. **Field** is the UI tester, which uses your app in a real browser and leaves tests behind. In the UI a portrait moves while its agent is working.

- **Each task should need you once.** Parallax counts how often that holds (`parallax stats`), and every way of starting a task goes through the same safety checks. From the app or the terminal, new or picked back up, no agent runs before your signed approval, a spending cap, a test of the sandbox and an entry in the ledger, Parallax's record of every step. One test proves that for every way in.
- **Every question comes ready to answer:** the options, what each one does, whose call it is and why a person has to make it, and a recommendation written by code, not by a model.
- **What an agent may reach is part of what you approve.** Network access, files outside its workspace and new dependencies are listed in the plan, and the sandbox is tested before every launch. A plan that asks for more never starts without you.
- **Code decides what happens next, not a model.** The ledger is tamper-evident: entries are never edited or removed, and each is chained by hash to the one before, so a change shows. Every approval is signed with a key the agents can't read.
- **Several tasks from one request.** Focus may split a request into parts, each a task with its own sandbox, cap and card, and code checks the split and runs parts that touch the same files one after another.
- **Focus and Maker are told what past tasks settled.** They get a short record that code writes from the ledger: for each accepted task, what it set out to do, from the intent you approved, and for every task, the reasons you gave when you sent work back, accepted a risk or dropped it. Second Eye and Reticle never see it, so each change is judged against its own intent.

## Try it

Linux, or Windows through WSL2 ([docs/wsl.md](docs/wsl.md) makes the distro); macOS is untested. You need root for `apt`, Node 22 and a logged-in Claude Code. What each step installs, the AppArmor `sysctl` behind step 1, and installing from a clone: [Setup](docs/using.md#setup).

1. The tools, as root. Apt's Node is too old for the sandbox runtime, so Node comes from NodeSource:
   ```bash
   apt update && apt install -y git bubblewrap socat ripgrep python3 curl ca-certificates
   curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && apt install -y nodejs
   npm install -g @anthropic-ai/sandbox-runtime
   sysctl -w kernel.apparmor_restrict_unprivileged_userns=0   # Ubuntu 24.04 and later; keep it in /etc/sysctl.d/99-parallax.conf
   ```
2. As your user, uv and Claude Code, then log in:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   curl -fsSL https://claude.ai/install.sh | bash
   source ~/.local/bin/env && claude
   ```
3. Parallax itself, from GitHub (the `parallax` on PyPI is an unrelated SSH tool). The latest release is [v0.6.0](https://github.com/mpwilso/parallax/releases/tag/v0.6.0):
   ```bash
   uv tool install "parallax[claude] @ git+https://github.com/mpwilso/parallax@v0.6.0"
   ```
4. `parallax doctor` checks the machine:
   ```
   platform      linux on wsl2                      ok
   sandbox       bubblewrap, socat, srt             ok
   claude login  found                              ok
   build tools   git, uv                            ok
   windows       interop off, path off, drives off  ok
   approval key  ~/.config/parallax/key             ok
   signing key   none: accept commits won't be signed
   repo          not in a git repo: run doctor in your repo to check it too
   ready.
   ```
5. In a git repo you want agents to work on: `parallax init`, then `parallax ui`. Type the task and answer the one card that comes back. Ready means the listed checks passed, not that the code is bug-free. The keys, the terminal commands, merging, Stop, spending, removal and the tests: [docs/using.md](docs/using.md).

## What was measured

- **Evals:** the 11 real fixes in the status line ran in one eval run, each reaching Ready with one decision, $9.68 in all; the one miss is a requirements gap, below ([the results](docs/evals.md#results)).
- **Seeded breaks:** against 23 mechanically broken versions of those fixes, Reticle or Second Eye caught 20, with 2 false alarms on the 11 real fixes ([docs/evals.md](docs/evals.md#the-checkers-together)). Those are seeded edits, not the mistakes Maker actually makes.
- **Real runs through the UI:** three tasks on this repo, one or two decisions each, $0.92 to $4.44; $27.71 spent across every attempt, the ones that found bugs included ([docs/ui-runs/](docs/ui-runs/README.md)).

## Limits

- **Second Eye reads the diff and can't run code.** It judges the outcome where the diff shows it, and says what it couldn't see. Reticle's tests do run: a test that passes on the code before the change is dropped, so every kept test was seen failing before the fix.
- **A requirements gap gets past every checker.** When Focus guesses wrong about what was wanted, every check passes the guess, because each one judges the work against that intent. That is the evals' one miss: the issue asked for hours to round to the nearest hour, and the maintainers' tests also checked other units.
- **One person, one machine.** There is no shared server or team inbox.
- **What agents know of the past is short.** It stops at the last ten finished tasks, and holds nothing but approved text and your own reasons.

The full list: [docs/using.md](docs/using.md#limits). What it protects and what it can't: [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md).

## How it was built

I designed Parallax and directed its build; Claude Code wrote most of the code under that direction. I reviewed every milestone, and the design notes and real run logs in [docs/](docs/) show the process. How it got here, milestone by milestone: [docs/history.md](docs/history.md). One early task it did on itself, before the hands-free redesign: [docs/example.md](docs/example.md).

## The other tools

This is one of six tools. Three run in order: [Loupe](https://github.com/mpwilso/loupe) writes the story, Parallax builds it in a sandbox and keeps the record, and [ISR](https://github.com/mpwilso/isr) tells the person accepting it what is left to check. Three stand alone: [Polarizer](https://github.com/mpwilso/polarizer) holds an agent's risky tool calls until a person approves them, [Mordecai](https://github.com/mpwilso/mordecai) judges whether a Claude skill helps, and [Max Q](https://github.com/mpwilso/Project-Max-Q) ranks job postings against a profile. Each runs on its own. Field's browser calls can go through Polarizer, off by default, and `parallax verify` checks Polarizer's ledger as well as its own ([docs/using.md](docs/using.md#field-through-polarizer)). The three were run in order once, on one change: [ISR's run log](https://github.com/mpwilso/isr/blob/master/docs/run-log.md).

## License

[MIT](LICENSE).
